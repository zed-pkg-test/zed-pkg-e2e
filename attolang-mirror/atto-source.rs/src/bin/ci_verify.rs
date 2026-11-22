use serde_json::Value;
use std::{env, fs, process};

fn fail(msg: impl AsRef<str>) -> ! {
    eprintln!("{}", msg.as_ref());
    process::exit(1);
}

fn read_json(path: &str) -> Value {
    let text = fs::read_to_string(path).unwrap_or_else(|e| fail(format!("read {path}: {e}")));
    serde_json::from_str(&text).unwrap_or_else(|e| fail(format!("parse {path}: {e}")))
}

fn rotr(x: u32, n: u32) -> u32 {
    x.rotate_right(n)
}

fn sha256(bytes: &[u8]) -> [u8; 32] {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut h = [
        0x6a09e667u32,
        0xbb67ae85,
        0x3c6ef372,
        0xa54ff53a,
        0x510e527f,
        0x9b05688c,
        0x1f83d9ab,
        0x5be0cd19,
    ];
    let bit_len = (bytes.len() as u64) * 8;
    let mut data = bytes.to_vec();
    data.push(0x80);
    while data.len() % 64 != 56 {
        data.push(0);
    }
    data.extend_from_slice(&bit_len.to_be_bytes());
    for chunk in data.as_chunks::<64>().0 {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes(chunk[i * 4..i * 4 + 4].try_into().unwrap());
        }
        for i in 16..64 {
            let s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            let s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh] = h;
        for i in 0..64 {
            let s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        let vals = [a, b, c, d, e, f, g, hh];
        for i in 0..8 {
            h[i] = h[i].wrapping_add(vals[i]);
        }
    }
    let mut out = [0u8; 32];
    for (i, x) in h.iter().enumerate() {
        out[i * 4..i * 4 + 4].copy_from_slice(&x.to_be_bytes());
    }
    out
}

fn hex(bytes: &[u8]) -> String {
    const H: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        s.push(H[(b >> 4) as usize] as char);
        s.push(H[(b & 15) as usize] as char);
    }
    s
}

fn verify_static_facts() {
    let src = fs::read("tests/fixtures/forward.ores")
        .unwrap_or_else(|e| fail(format!("read fixture: {e}")));
    let d = read_json("target/static-facts-v0.json");
    assert_eq!(d["version"], "ores-static-facts/v0");
    assert_eq!(d["unit"], format!("sha256:{}", hex(&sha256(&src))));
    let edges = d["edges"]
        .as_array()
        .unwrap_or_else(|| fail("edges must be array"));
    assert!(
        !edges.is_empty()
            && edges
                .iter()
                .all(|e| e["kind"] == "call" && e["guard"] == "unproved")
    );
    let procedures = d["procedures"]
        .as_array()
        .unwrap_or_else(|| fail("procedures must be array"));
    assert!(procedures
        .iter()
        .all(|p| p["loops"].as_array().is_some_and(|v| v.is_empty())));
    for key in ["taint_sources", "flows", "sinks", "permission_checks"] {
        assert!(
            d[key].as_array().is_some_and(|v| v.is_empty()),
            "{key} must be empty"
        );
    }
}

fn verify_effects() {
    let d = read_json("target/effects.json");
    assert_eq!(d["schema_version"], 1);
    let ops = d["functions"][0]["operations"]
        .as_array()
        .unwrap_or_else(|| fail("operations must be array"));
    assert!(ops
        .iter()
        .any(|x| x["kind"] == "call" && x["target"] == "times"));
}

fn verify_cfg(path: &str, require_call: bool) {
    let x = read_json(path);
    assert_eq!(x["schema_version"], 2);
    if require_call {
        let events = x["functions"][0]["blocks"][0]["events"]
            .as_array()
            .unwrap_or_else(|| fail("events must be array"));
        assert!(events.iter().any(|e| e["kind"] == "call"));
    } else {
        assert_eq!(
            x["functions"][0]["blocks"][0]["terminator"]["kind"],
            "return"
        );
    }
}

fn main() {
    match env::args().nth(1).as_deref() {
        Some("static-facts") => verify_static_facts(),
        Some("effects") => verify_effects(),
        Some("safety") => {
            let scalar = read_json("target/safety.json");
            assert_eq!(scalar["effects_complete"], true);
            assert_eq!(scalar["functions"][0]["control_flow"], "linear");
            assert_eq!(scalar["functions"][0]["max_uninterrupted_units"], 16);
            let calls = read_json("target/safety-calls.json");
            assert!(calls["functions"][0]["events"].as_array().unwrap().iter().any(|event| event["kind"] == "call"));
        },
        Some("scalar-flow") => assert_eq!(read_json("target/flow.json"), read_json("tests/fixtures/flow-scalar.json")),
        Some("paired-effects") => assert_eq!(
            read_json("target/paired-forward.effects.json"),
            read_json("tests/fixtures/forward.effects.json"),
            "analyzer v1 golden drift"
        ),
        Some("cfg") => verify_cfg("target/cfg-v2.json", false),
        Some("cfg-call") => verify_cfg("target/cfg-v2-call.json", true),
        Some("oversized") => fs::write("target/oversized.ores", vec![b'x'; 1_048_578])
            .unwrap_or_else(|e| fail(format!("write oversized fixture: {e}"))),
        _ => fail("usage: ci_verify <static-facts|effects|safety|scalar-flow|paired-effects|cfg|cfg-call|oversized>"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn sha256_known_vector() {
        assert_eq!(
            hex(&sha256(b"abc")),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
