//! Advisory common v0 facts from the *real* checked scalar MIR, not regex syntax.
use crate::{frontend::Expr, mir::Module};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};

fn sha256_hex(bytes: &[u8]) -> String {
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
    let bitlen = (bytes.len() as u64).wrapping_mul(8);
    let mut input = bytes.to_vec();
    input.push(0x80);
    while input.len() % 64 != 56 {
        input.push(0);
    }
    input.extend_from_slice(&bitlen.to_be_bytes());
    for chunk in input.as_chunks::<64>().0 {
        let mut w = [0u32; 64];
        for (i, word) in w.iter_mut().enumerate().take(16) {
            *word = u32::from_be_bytes(chunk[i * 4..i * 4 + 4].try_into().expect("four bytes"));
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut z] = h;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let t1 = z
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            z = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        for (dst, add) in h.iter_mut().zip([a, b, c, d, e, f, g, z]) {
            *dst = dst.wrapping_add(add);
        }
    }
    h.iter().map(|x| format!("{x:08x}")).collect()
}

/// Only effect operations supported by the MIR grammar are included.
/// No scheduler, actor, memory, security, or work bounds are asserted.
pub fn export(module: &Module, source: &str) -> Result<Value, String> {
    if source.len() > 1_048_576 || source.starts_with('\u{feff}') || source.contains('\0') {
        return Err("invalid/bom/oversized exact source".into());
    }
    if module.functions.is_empty() || module.functions.len() > 512 {
        return Err("procedure budget exceeded".into());
    }
    let mut sym = HashMap::new();
    let mut procedures = Vec::new();
    for f in &module.functions {
        if f.symbol.len() > 200 || f.params.len() > 128 {
            return Err("symbol/arity budget exceeded".into());
        }
        let id = format!("__root__.{}/{}", f.symbol, f.params.len());
        if sym.insert(f.symbol.as_str(), id.clone()).is_some() {
            return Err("duplicate function identity".into());
        }
        procedures.push(json!({"id":id,"loops":[]}));
    }
    let mut edges = Vec::new();
    let mut seen = HashSet::new();
    let mut visited = 0usize;
    for f in &module.functions {
        let caller = sym.get(f.symbol.as_str()).expect("checked header").clone();
        let mut stack: Vec<(&Expr, usize)> = Vec::new();
        for (_, expr) in &f.locals {
            stack.push((expr, 0));
        }
        stack.push((&f.return_value, 0));
        while let Some((expr, depth)) = stack.pop() {
            visited += 1;
            if visited > 30_000 || depth > 128 {
                return Err("fact traversal budget exceeded".into());
            }
            match expr {
                Expr::Integer(_) | Expr::Name(_) => {}
                Expr::Negate(e) => stack.push((e, depth + 1)),
                Expr::Add(a, b) | Expr::Subtract(a, b) | Expr::Multiply(a, b) => {
                    stack.push((a, depth + 1));
                    stack.push((b, depth + 1));
                }
                Expr::Call { name, args } => {
                    let callee = sym
                        .get(name.as_str())
                        .ok_or("unresolved callee in checked MIR")?;
                    if !seen.insert((caller.clone(), callee.clone())) {
                        return Err(
                            "duplicate caller/callee callsite requires callsite facts".into()
                        );
                    }
                    edges.push(json!({"from":caller,"to":callee,"kind":"call","fanout":1,"guard":"unproved"}));
                    if edges.len() > 8_192 {
                        return Err("call edge budget exceeded".into());
                    }
                    for arg in args {
                        stack.push((arg, depth + 1));
                    }
                }
            }
        }
    }
    procedures.sort_by_key(|v| v["id"].as_str().unwrap_or("").to_owned());
    edges.sort_by_key(|v| {
        (
            v["from"].as_str().unwrap_or("").to_owned(),
            v["to"].as_str().unwrap_or("").to_owned(),
        )
    });
    Ok(json!({
        "version":"ores-static-facts/v0",
        "unit":format!("sha256:{}",sha256_hex(source.as_bytes())),
        "procedures":procedures,"edges":edges,
        "taint_sources":[],"flows":[],"sinks":[],"permission_checks":[]
    }))
}
#[cfg(test)]
mod tests {
    use super::*;
    fn facts(src: &str) -> Result<Value, String> {
        let ast = crate::frontend::parse(src).map_err(|x| x.to_string())?;
        let ir = crate::mir::lower(ast).map_err(|x| x.to_string())?;
        export(&ir, src)
    }
    #[test]
    fn sha_matches_standard_vectors() {
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
    #[test]
    fn source_to_verified_mir_facts() {
        let source =
            "pub fnc main(): int { return add(40,2); } fnc add(int a,int b):int{return a+b;}";
        let doc = facts(source).unwrap();
        assert_eq!(doc["version"], "ores-static-facts/v0");
        assert_eq!(doc["procedures"].as_array().unwrap().len(), 2);
        assert_eq!(doc["edges"][0]["from"], "__root__.main/0");
        assert_eq!(doc["edges"][0]["to"], "__root__.add/2");
        assert_eq!(doc["edges"][0]["guard"], "unproved");
    }
    #[test]
    fn duplicate_callsite_and_unsupported_effects_fail_closed() {
        assert!(facts("fnc x():int{return 1;} pub fnc main():int{return x()+x();}").is_err());
        assert!(facts("pub fnc main():int{return missing(2);}").is_err());
        assert!(facts("define actor A as end").is_err());
    }
}
