#!/usr/bin/env python3
"""Compiled .atto ObjectStorage M1 cross-backend tests (not a host-only model).
This deliberately bounded single-actor shape is not general Atto compiler parity.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

argp = argparse.ArgumentParser()
argp.add_argument("--backend", choices=("java", "llvm"), required=True)
argp.add_argument("--generator", required=True)
argp.add_argument("--fixture", required=True)
argp.add_argument("--java-arena")
argp.add_argument("--java", default="java")
argp.add_argument("--javac", default="javac")
argp.add_argument("--llvm-bridge")
argp.add_argument("--llvm-include")
argp.add_argument("--clangxx", default="clang++")
args = argp.parse_args()
GENERATOR = Path(args.generator).resolve()
FIXTURE = Path(args.fixture).resolve()
BASE = FIXTURE.read_text(encoding="utf-8")


def run(command, timeout=120):
    return subprocess.run([str(x) for x in command], capture_output=True,
                          timeout=timeout, check=False)


class ObjectM1CompilerGuests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atto-object-m1-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def compile(self, source, tag):
        path = self.root / (tag + ".atto")
        path.write_text(source, encoding="utf-8")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        out = self.root / (tag + (".java" if args.backend == "java" else ".ll"))
        result = run(["python3", GENERATOR, path, "--source-sha256", sha,
                      "--backend", args.backend, "--out", out])
        return result, out, sha

    def execute(self, out, tag):
        if args.backend == "java":
            self.assertTrue(args.java_arena, "checked Java arena path required")
            classes = self.root / ("classes-" + tag)
            classes.mkdir()
            source = classes / "AttoObjectStorageM1.java"
            source.write_bytes(out.read_bytes())
            compiled = run([args.javac, "-Xlint:all", "-Werror", "-d", classes,
                            args.java_arena, source])
            self.assertEqual(compiled.returncode, 0,
                             compiled.stderr.decode(errors="replace")[-3000:])
            return run([args.java, "--enable-native-access=ALL-UNNAMED",
                        "-cp", classes, "AttoObjectStorageM1"])
        self.assertTrue(args.llvm_bridge and args.llvm_include,
                        "checked LLVM heap bridge and include path required")
        target = self.root / (tag + ".native")
        compiled = run([args.clangxx, "-std=c++17", "-O1", "-pthread",
                        "-Wno-override-module", "-I" + args.llvm_include,
                        "-x", "ir", out, "-x", "c++", args.llvm_bridge,
                        "-o", target], timeout=180)
        self.assertEqual(compiled.returncode, 0,
                         compiled.stderr.decode(errors="replace")[-3000:])
        self.assertTrue(target.is_file() and target.stat().st_size > 0)
        return run([target])

    def test_positive_compiled_guest(self):
        cases = [
            ("point_particle_13", BASE, 13),
            ("point_particle_46",
             BASE.replace("x=10", "x=7").replace("y=20", "y=11")
                 .replace("velocity=3", "velocity=42")
                 .replace("particle.position.x + particle.velocity",
                          "particle.position.y + particle.velocity - point.x"),
             46),
        ]
        for name, text, expected in cases:
            with self.subTest(name=name):
                result, out, sha = self.compile(text, name)
                self.assertEqual(result.returncode, 0,
                    result.stderr.decode(errors="replace")[-1000:])
                self.assertTrue(out.is_file() and out.stat().st_size > 100)
                generated = out.read_text(encoding="utf-8")
                if args.backend == "java":
                    self.assertIn("arena.allocate(actor, 101)", generated)
                    self.assertIn("arena.release(particle, actor, 101)", generated)
                    self.assertIn("arena.read_i64(", generated)
                else:
                    for entry in ("@atto_m1_allocate", "@atto_m1_write",
                                  "@atto_m1_read", "@atto_m1_finish",
                                  "@llvm.sadd.with.overflow.i64"):
                        self.assertIn(entry, generated)
                executed = self.execute(out, name)
                self.assertEqual(executed.returncode, expected,
                     executed.stderr.decode(errors="replace")[-2000:])
                self.assertEqual(executed.stdout.decode().splitlines(), [
                    "ATTO_OBJECT_M1_RESULT=" + str(expected),
                    "ATTO_OBJECT_M1_SCOPE=CLOSED_WORLD_SINGLE_ACTOR"
                ])
                print("ATTO_OBJECT_M1_NATIVE_GUEST_PASS backend=" + args.backend +
                      " case=" + name + " fixture_sha=" + sha, flush=True)

    def test_negative_no_artifact(self):
        cases = [
            ("unknown_struct_slot", BASE.replace("int y;", "int z;")),
            ("invalid_owner", BASE.replace("position=point", "position=particle")),
            ("missing_constructor", BASE.replace("new Particle", "Particle")),
            ("forged_int_field", BASE.replace("int velocity;", "Point velocity;")),
            ("bad_field_path", BASE.replace("particle.position.x", "particle.position.z")),
            ("duplicate_nominal", BASE + "define struct Point as int x; int y; end"),
            ("guest_send", BASE.replace("return particle.position.x",
                "particle.send(99); return particle.position.x")),
            ("unsupported_cast", BASE.replace("particle.position.x", "particle as int")),
            ("out_of_range_literal", BASE.replace("x=10", "x=9223372036854775808")),
            ("checked_arithmetic_overflow", BASE.replace("x=10", "x=9223372036854775807")),
        ]
        for name, text in cases:
            with self.subTest(name=name):
                result, out, _ = self.compile(text, "reject_" + name)
                self.assertNotEqual(result.returncode, 0, name)
                self.assertFalse(out.exists(), "rejected guest emitted artifact: " + name)
                self.assertIn(b"ATTO_OBJECT_M1_REJECT", result.stderr)

    def test_existing_output_is_never_replaced(self):
        path = self.root / "existing.atto"
        path.write_text(BASE, encoding="utf-8")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        dest = self.root / ("occupied.java" if args.backend == "java" else "occupied.ll")
        dest.write_text("DO_NOT_REPLACE", encoding="utf-8")
        result = run(["python3", GENERATOR, path, "--source-sha256", sha,
                      "--backend", args.backend, "--out", dest])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(dest.read_text(encoding="utf-8"), "DO_NOT_REPLACE")

    def test_atomic_no_clobber_against_publication_race(self):
        source = self.root / "race.atto"
        source.write_text(BASE, encoding="utf-8")
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        dest = self.root / ("race.java" if args.backend == "java" else "race.ll")
        spec = importlib.util.spec_from_file_location("object_m1_for_test", GENERATOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_link = module.os.link
        observed = []

        def plant_destination(temp_path, final_path, **kwargs):
            observed.append((Path(temp_path), Path(final_path)))
            dest.write_text("ATTACKER_OWNED", encoding="utf-8")
            return real_link(temp_path, final_path, **kwargs)

        argv = [str(GENERATOR), str(source), "--source-sha256", sha,
                "--backend", args.backend, "--out", str(dest)]
        with mock.patch.object(module.os, "link", side_effect=plant_destination):
            with mock.patch("sys.argv", argv):
                self.assertEqual(module.main(), 2)
        self.assertEqual(len(observed), 1)
        self.assertEqual(dest.read_text(encoding="utf-8"), "ATTACKER_OWNED")
        self.assertFalse(list(self.root.glob(".atto-object-m1-*")),
                         "private intermediate artifact leaked after failed publish")

    def test_source_sha_rejection(self):
        path = self.root / "sha.atto"
        path.write_text(BASE, encoding="utf-8")
        dest = self.root / ("bad.java" if args.backend == "java" else "bad.ll")
        result = run(["python3", GENERATOR, path, "--source-sha256", "0" * 64,
                      "--backend", args.backend, "--out", dest])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2, argv=[__file__])
