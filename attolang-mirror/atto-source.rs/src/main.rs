use oreslang_source_rs::{
    cfg, codegen, effects, flow, frontend, mir,
    profile::{self, BuildProfile},
    safety,
};
use std::{
    env, fs,
    io::{Read, Write},
    path::Path,
    process::ExitCode,
};

// Keep file reading and frontend parsing on exactly the same source-byte budget.
const MAX_SOURCE_BYTES: usize = frontend::MAX_SOURCE_BYTES;

fn canonical_destination(path: &str) -> Result<std::path::PathBuf, String> {
    let destination = Path::new(path);
    let parent = destination
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let directory = fs::canonicalize(parent).map_err(|e| e.to_string())?;
    let basename = destination
        .file_name()
        .ok_or("output requires a filename")?;
    let resolved = directory.join(basename);
    // Never follow the final output component, even for an existing path.
    // A symlink (including a dangling one) must not redirect compiler writes.
    match fs::symlink_metadata(&resolved) {
        Ok(metadata) if metadata.file_type().is_symlink() => {
            Err("refusing symlinked compiler output".into())
        }
        Ok(metadata) if !metadata.is_file() => Err("compiler output must be a regular file".into()),
        Ok(_) => Ok(resolved),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(resolved),
        Err(error) => Err(error.to_string()),
    }
}

/// Publish a complete artifact with create-new staging and same-directory
/// rename. In particular, replacing a hardlinked destination must never
/// truncate its other link (which could be the source file).
fn publish_artifact(path: &str, bytes: &[u8]) -> Result<(), String> {
    let destination = canonical_destination(path)?;
    let parent = destination.parent().ok_or("output requires a parent")?;
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    for attempt in 0..64_u8 {
        let temporary = parent.join(format!(
            ".oresc-{}-{nonce}-{attempt}.tmp",
            std::process::id()
        ));
        let mut file = match fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
        {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.to_string()),
        };
        let result = (|| {
            file.write_all(bytes).map_err(|e| e.to_string())?;
            file.sync_all().map_err(|e| e.to_string())?;
            drop(file);
            // Recheck for a clear diagnostic. rename() replaces the final
            // directory entry without following its symlink in any case.
            canonical_destination(path)?;
            fs::rename(&temporary, &destination).map_err(|e| e.to_string())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        return result;
    }
    Err("unable to allocate unique compiler artifact staging file".into())
}

fn main() -> ExitCode {
    let raw_args: Vec<String> = env::args().collect();
    let result: Result<(), String> = (|| {
        let mut profile = BuildProfile::Dev;
        let mut profile_seen = false;
        let mut freestanding = false;
        let mut checked_i64 = false;
        let mut args = Vec::with_capacity(raw_args.len());
        args.push(raw_args[0].clone());

        for arg in raw_args.iter().skip(1) {
            if let Some(selected) = BuildProfile::parse_flag(arg) {
                if profile_seen {
                    return Err(
                        "--release, --strict-aot and --hard-rt are mutually exclusive".into(),
                    );
                }
                profile = selected;
                profile_seen = true;
            } else if arg == "--freestanding" {
                if freestanding {
                    return Err("duplicate --freestanding".into());
                }
                freestanding = true;
            } else if arg == "--checked-i64" {
                if checked_i64 {
                    return Err("duplicate --checked-i64".into());
                }
                checked_i64 = true;
            } else {
                args.push(arg.clone());
            }
        }

        let (mode, input, output, effects_output, budget) = match args.as_slice() {
            [_, flag, input] if flag == "--safety-ir" => ("safety", input.as_str(), None, None, None),
            [_, flag, input, units] if flag == "--safety-ir" => ("safety", input.as_str(), None, None,
                Some(units.parse::<u64>().map_err(|_| "invalid abstract compute budget")?)),
            [_, flag, units, input] if flag == "--flow" => ("flow", input.as_str(), None, None,
                Some(units.parse::<u64>().map_err(|_| "invalid abstract compute budget")?)),
            [_, flag, input] if flag == "--check" || flag == "--effects" || flag == "--static-facts" =>
                (flag.as_str(), input.as_str(), None, None, None),
            [_, flag, input, object, effects] if flag == "--compile-with-effects" =>
                (flag.as_str(), input.as_str(), Some(object.as_str()), Some(effects.as_str()), None),
            [_, flag, input] if flag == "--cfg-ir" => ("cfg", input.as_str(), None, None, None),
            [_, flag, input, units] if flag == "--cfg-ir" => ("cfg", input.as_str(), None, None,
                Some(units.parse::<u64>().map_err(|_| "invalid abstract compute budget")?)),
            [_, input, output] if !input.starts_with("--") =>
                ("object", input.as_str(), Some(output.as_str()), None, None),
            _ => return Err("usage: oresc [--release|--strict-aot|--hard-rt] [--freestanding] [--checked-i64] --safety-ir <source.ores> [abstract-budget] | --flow <max-uninterrupted-units> <source.ores> | --check <source.ores> | --effects <source.ores> | --static-facts <source.ores> | --cfg-ir <source.ores> [abstract-budget] | --compile-with-effects <source.ores> <out.o> <out.json> | <source.ores> <out.o>".into()),
        };
        // Current analysis exporters model only linear scalar computations.
        // Refuse to issue falsely complete facts for checked overflow traps.
        if checked_i64 && mode != "object" {
            return Err("--checked-i64 cannot be combined with safety/effect/CFG/fact export: conditional overflow traps are not yet modeled".into());
        }
        let source_id = fs::canonicalize(input).map_err(|e| e.to_string())?;
        let object_id = if let Some(path) = output {
            let out = canonical_destination(path)?;
            if out == source_id {
                return Err("refusing to overwrite source with compiler output".into());
            }
            Some(out)
        } else {
            None
        };
        if let Some(path) = effects_output {
            let facts_id = canonical_destination(path)?;
            if facts_id == source_id || object_id.as_ref() == Some(&facts_id) {
                return Err("source, object and effect output files must be distinct".into());
            }
        }
        // Bound both ordinary and paired compiler invocations before tokenization.
        let mut source = String::new();
        fs::File::open(input)
            .map_err(|e| e.to_string())?
            .take(MAX_SOURCE_BYTES as u64 + 1)
            .read_to_string(&mut source)
            .map_err(|e| e.to_string())?;
        if source.len() > MAX_SOURCE_BYTES {
            return Err(format!(
                "source too large: exceeds {MAX_SOURCE_BYTES}-byte admission budget"
            ));
        }
        let parsed = frontend::parse(&source).map_err(|e| e.to_string())?;
        let checked = mir::lower(parsed).map_err(|e| e.to_string())?;
        let _freestanding = freestanding;
        let checked = profile::apply(checked, profile)?;
        if mode == "--compile-with-effects" {
            let object = codegen::emit_object(&checked)?;
            let facts = effects::export_effect_ir_checked(&checked)?;
            let serialized = serde_json::to_vec(&facts).map_err(|e| e.to_string())?;
            // All compilation completes before writing either artifact. These
            // two individually atomic file publishes are not a joint transaction.
            publish_artifact(output.ok_or("missing native output")?, &object)?;
            publish_artifact(effects_output.ok_or("missing effect output")?, &serialized)?;
        } else {
            match mode {
                "--check" => println!("OK: {} function(s)", checked.functions.len()),
                "safety" => println!(
                    "{}",
                    serde_json::to_string_pretty(&safety::export_safety_ir(&checked, budget))
                        .map_err(|e| e.to_string())?
                ),
                "flow" => println!(
                    "{}",
                    serde_json::to_string_pretty(&flow::export_scalar_flow(
                        &checked,
                        budget.ok_or("missing abstract work budget")?
                    )?)
                    .map_err(|e| e.to_string())?
                ),
                "cfg" => println!(
                    "{}",
                    serde_json::to_string_pretty(&cfg::export_cfg_v2(&checked, budget)?)
                        .map_err(|e| e.to_string())?
                ),
                "--static-facts" => println!(
                    "{}",
                    serde_json::to_string(&oreslang_source_rs::static_facts::export(
                        &checked, &source
                    )?)
                    .map_err(|e| e.to_string())?
                ),
                "--effects" => println!(
                    "{}",
                    serde_json::to_string_pretty(&effects::export_effect_ir_checked(&checked)?)
                        .map_err(|e| e.to_string())?
                ),
                _ => {
                    let bytes = if checked_i64 {
                        // Structural recursion/resource budget gate, not a
                        // checked-arithmetic effect or exception certificate.
                        let _ = effects::export_effect_ir_checked(&checked)?;
                        codegen::emit_object_checked_i64(&checked)?
                    } else {
                        effects::export_effect_ir_checked(&checked)?;
                        codegen::emit_object(&checked)?
                    };
                    publish_artifact(output.ok_or("missing output path")?, &bytes)?;
                }
            }
        }
        Ok(())
    })();
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("oresc: {error}");
            ExitCode::from(1)
        }
    }
}
