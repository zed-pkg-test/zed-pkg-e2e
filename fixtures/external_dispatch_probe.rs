use std::env;
use std::fs;
use std::path::PathBuf;
use std::process;

fn json_string(value: &str) -> String {
    let mut output = String::with_capacity(value.len() + 2);
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character <= '\u{001f}' => {
                use std::fmt::Write as _;
                write!(&mut output, "\\u{:04x}", character as u32)
                    .expect("writing to a String cannot fail");
            }
            character => output.push(character),
        }
    }
    output.push('"');
    output
}

fn main() {
    let output = env::var_os("ZED_PROBE_OUTPUT")
        .map(PathBuf::from)
        .expect("ZED_PROBE_OUTPUT must name the evidence file");
    let arguments = env::args_os()
        .skip(1)
        .map(|argument| argument.to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    let environment = [
        "ZED_EXTERNAL_SUBCOMMAND",
        "ZED_PKG_HOME",
        "ZED_PKG_GIT_SUBMODULES",
    ];

    let mut payload = String::from("{\"args\":[");
    for (index, argument) in arguments.iter().enumerate() {
        if index != 0 {
            payload.push(',');
        }
        payload.push_str(&json_string(argument));
    }
    payload.push_str("],\"env\":{");
    for (index, key) in environment.iter().enumerate() {
        if index != 0 {
            payload.push(',');
        }
        payload.push_str(&json_string(key));
        payload.push(':');
        payload.push_str(&json_string(&env::var(key).unwrap_or_default()));
    }
    payload.push_str("}}\n");

    fs::write(output, payload).expect("write probe evidence");
    let exit_code = env::var("ZED_PROBE_EXIT")
        .ok()
        .and_then(|value| value.parse::<i32>().ok())
        .unwrap_or(0);
    process::exit(exit_code);
}
