//! Dependency-free M9 supervisor notice wire conformance and executable
//! stdout verifier. Rust-only test harness; no Python dependency.
use std::fmt::Write as _;
use std::fs;

const MAX_SAFE: u64 = 9_007_199_254_740_991;
const STOP_GOLDEN: &str = include_str!("fixtures/m9/stop.json");
const PANIC_GOLDEN: &str = include_str!("fixtures/m9/panic.json");
const POLICY: &str = include_str!("../contracts/m9-supervisor-control-v1.json");
const WIRE: &str = include_str!("../contracts/m9-supervisor-notice-wire-v1.json");

#[derive(Clone, Debug)]
struct Notice<'a> {
    kind: &'a str,
    actor_id: u64,
    generation: u64,
    supervisor_id: u64,
    message_id: u64,
    fault_code: u32,
    reason: &'a str,
}

impl Notice<'_> {
    fn to_wire_json(&self) -> Result<String, &'static str> {
        if self.kind != "STOP" && self.kind != "PANIC" {
            return Err("kind");
        }
        if self.actor_id > MAX_SAFE
            || self.generation == 0 || self.generation > MAX_SAFE
            || self.supervisor_id > MAX_SAFE || self.message_id > MAX_SAFE
        {
            return Err("integer bounds");
        }
        if (self.kind == "STOP" && (self.message_id != 0 || self.fault_code != 0))
            || (self.kind == "PANIC" && !matches!(self.fault_code, 1 | 2 | 100 | 101))
        {
            return Err("fault and message identity");
        }
        if self.reason.is_empty() || self.reason.len() > 128 {
            return Err("reason bytes");
        }
        let mut quoted = String::from("\"");
        for c in self.reason.chars() {
            match c {
                '"' => quoted.push_str("\\\""),
                '\\' => quoted.push_str("\\\\"),
                '\n' => quoted.push_str("\\n"),
                '\r' => quoted.push_str("\\r"),
                '\t' => quoted.push_str("\\t"),
                '\u{0008}' => quoted.push_str("\\b"),
                '\u{000C}' => quoted.push_str("\\f"),
                '\u{0000}'..='\u{001F}' => { write!(&mut quoted, "\\u{:04x}", c as u32).unwrap(); }
                _ => quoted.push(c),
            }
        }
        quoted.push('"');
        Ok(format!(
            "{{\"kind\":\"{}\",\"actor_id\":{},\"generation\":{},\"supervisor_id\":{},\"message_id\":{},\"fault_code\":{},\"reason\":{}}}",
            self.kind, self.actor_id, self.generation, self.supervisor_id,
            self.message_id, self.fault_code, quoted
        ))
    }
}
fn stop<'a>() -> Notice<'a> {
    Notice {kind:"STOP",actor_id:0,generation:1,supervisor_id:777,
        message_id:0,fault_code:0,reason:"graceful stop"}
}
fn panic<'a>() -> Notice<'a> {
    Notice {kind:"PANIC",actor_id:0,generation:1,supervisor_id:777,
        message_id:1,fault_code:1,reason:"checked arithmetic"}
}
fn golden(s: &str) -> &str {s.trim_end_matches('\n')}
fn exactly_one_line(text: &str, prefix: &str, expected: &str) -> Result<(), String> {
    let lines: Vec<&str> = text.lines().filter_map(|line| line.strip_prefix(prefix)).collect();
    if lines.len() != 1 || lines[0] != expected {
        return Err(format!("expected exactly one {prefix}{expected}, got {lines:?}"));
    }
    Ok(())
}
fn verify_jvm_stdout(text: &str) -> Result<(), String> {
    exactly_one_line(text, "M9_WIRE_STOP_JSON=", golden(STOP_GOLDEN))?;
    exactly_one_line(text, "M9_WIRE_PANIC_JSON=", golden(PANIC_GOLDEN))?;
    exactly_one_line(text, "M9_JAVA_PRIORITY_STOP_THEN_PANIC_NOTICE=", "PASS")?;
    exactly_one_line(text, "M9_JAVA_REINCARNATION_FENCE=", "PASS")?;
    Ok(())
}
fn main() {
    let mut args = std::env::args().skip(1);
    match (args.next().as_deref(), args.next(), args.next()) {
        (Some("--verify-output"), Some(path), None) => {
            let content = fs::read_to_string(path).expect("missing captured JVM/Native stdout");
            verify_jvm_stdout(&content).unwrap_or_else(|e| panic!("{e}"));
            println!("M9_RUST_WIRE_EXECUTABLE_STDOUT_CONFORMANCE=PASS");
        }
        _ => { eprintln!("usage: m9_notice_wire --verify-output <captured stdout>"); std::process::exit(2); }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn canonical_stop_fixture() {
        assert_eq!(stop().to_wire_json().unwrap(), golden(STOP_GOLDEN));
    }
    #[test] fn canonical_panic_fixture() {
        assert_eq!(panic().to_wire_json().unwrap(), golden(PANIC_GOLDEN));
    }
    #[test] fn control_kind_and_codes_cannot_be_forged() {
        let mut v = stop(); v.kind = "DONE"; assert!(v.to_wire_json().is_err());
        v = stop();v.message_id=1;assert!(v.to_wire_json().is_err());
        v = stop();v.fault_code=1;assert!(v.to_wire_json().is_err());
        v = panic();v.fault_code=0;assert!(v.to_wire_json().is_err());
        v = panic();v.fault_code=102;assert!(v.to_wire_json().is_err());
    }
    #[test] fn numeric_identity_is_bounded_and_generation_nonzero() {
        let mut v = panic();v.generation=0;assert!(v.to_wire_json().is_err());
        v=panic();v.actor_id=MAX_SAFE+1;assert!(v.to_wire_json().is_err());
        v=panic();v.message_id=MAX_SAFE+1;assert!(v.to_wire_json().is_err());
        v=panic();v.supervisor_id=MAX_SAFE+1;assert!(v.to_wire_json().is_err());
        v=panic();v.generation=MAX_SAFE+1;assert!(v.to_wire_json().is_err());
        v=panic();v.actor_id=MAX_SAFE;assert!(v.to_wire_json().is_ok());
    }
    #[test] fn reason_is_bounded_and_escaped() {
        let mut v = panic();v.reason="";assert!(v.to_wire_json().is_err());
        v=panic();v.reason="okay";assert!(v.to_wire_json().is_ok());
    }
    #[test] fn escaped_reason_preserves_json_safety() {
        let mut v=panic();v.reason="line\nquote\"slash\\";
        assert!(v.to_wire_json().unwrap().ends_with("\"reason\":\"line\\nquote\\\"slash\\\\\"}"));
        v.reason="\u{0001}";
        assert!(v.to_wire_json().unwrap().contains("\"reason\":\"\\u0001\""));
    }
    #[test] fn reasons_over_128_utf8_bytes_fail() {
        let long="a".repeat(129);
        let mut v=panic();v.reason=&long;assert!(v.to_wire_json().is_err());
        let non_ascii="é".repeat(65);
        v.reason=&non_ascii;assert!(v.to_wire_json().is_err());
    }
    #[test] fn m9_schema_declares_all_wire_fields() {
        for name in ["kind","actor_id","generation","supervisor_id",
            "message_id","fault_code","reason"] {
            assert!(WIRE.contains(&format!("\"{name}\"")),"missing field {name}");
        }
        assert!(WIRE.contains("9007199254740991"));
        assert!(WIRE.contains("atto.m9.supervisor-notice-wire.v1"));
    }
    #[test] fn previous_m9_control_and_conservation_contract_retained() {
        for term in ["independent of bounded DATA", "queued DATA as ABANDONED",
            "current DATA as FAILED","at most one PANIC","STOP-to-PANIC",
            "stale actor generation","physically quiesce","YIELD retains one",
            "accepted = completed + failed + abandoned + queued + in_flight"] {
            assert!(POLICY.contains(term),"missing legacy M9 rule: {term}");
        }
        assert!(POLICY.contains("\"general_actor\": false"));
        assert!(POLICY.contains("\"max\": 10000"));
        assert!(POLICY.contains("\"max\": 4"));
        assert!(POLICY.contains("\"await_select\": false"));
        assert!(POLICY.contains("\"hard_real_time\": false"));
    }
    #[test] fn stdout_verifier_requires_one_of_each_event() {
        let good=format!("M9_WIRE_STOP_JSON={}\nM9_WIRE_PANIC_JSON={}\nM9_JAVA_PRIORITY_STOP_THEN_PANIC_NOTICE=PASS\nM9_JAVA_REINCARNATION_FENCE=PASS\n",
            golden(STOP_GOLDEN),golden(PANIC_GOLDEN));
        assert!(verify_jvm_stdout(&good).is_ok());
        assert!(verify_jvm_stdout(&good.replace("M9_WIRE_PANIC_JSON=","M9_WIRE_PANIC_BAD=")).is_err());
        assert!(verify_jvm_stdout(&(good.clone()+&good)).is_err());
        assert!(verify_jvm_stdout(&good.replace("\"actor_id\":0","\"actor_id\":9")).is_err());
    }
}
