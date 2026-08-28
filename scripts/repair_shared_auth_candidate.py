#!/usr/bin/env python3

from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} drifted: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1))


handoff = Path("src/handoff/mod.rs")
text = handoff.read_text()
cloud_only = '''        let endpoint = format!(
            "https://{}.supabase.co/auth/v1/token?grant_type=password",
            project.project_ref
        );'''
if text.count(cloud_only) != 1:
    raise SystemExit(
        f"cloud-only Supabase endpoint drifted: found {text.count(cloud_only)} matches"
    )
text = text.replace(
    cloud_only,
    "        let endpoint = supabase_password_token_endpoint(project);",
    1,
)
helper = '''fn supabase_password_token_endpoint(project: &SupabaseProject) -> String {
    format!(
        "{}/token?grant_type=password",
        project.issuer().trim_end_matches('/')
    )
}

'''
anchor = "impl HandoffService {\n"
if "fn supabase_password_token_endpoint(" in text:
    raise SystemExit("endpoint helper unexpectedly exists at the locked source head")
if text.count(anchor) != 1:
    raise SystemExit("HandoffService implementation anchor drifted")
text = text.replace(anchor, helper + anchor, 1)
regression = r'''

#[cfg(test)]
mod endpoint_tests {
    use serde_json::json;

    use super::supabase_password_token_endpoint;
    use crate::config::SupabaseProject;

    fn project(project_ref: &str, issuer: Option<&str>) -> SupabaseProject {
        let mut value = json!({
            "name": "zpkg-test",
            "project_ref": project_ref
        });
        if let Some(issuer) = issuer {
            value["issuer"] = json!(issuer);
        }
        serde_json::from_value(value).expect("valid Supabase project fixture")
    }

    #[test]
    fn derives_hosted_password_endpoint_from_default_issuer() {
        let project = project("abcdefghijklmnopqrst", None);
        assert_eq!(
            supabase_password_token_endpoint(&project),
            "https://abcdefghijklmnopqrst.supabase.co/auth/v1/token?grant_type=password"
        );
    }

    #[test]
    fn honors_explicit_self_hosted_issuer_and_trims_trailing_slash() {
        let project = project(
            "unused-for-explicit-issuer",
            Some("http://127.0.0.1:54321/auth/v1/"),
        );
        assert_eq!(
            supabase_password_token_endpoint(&project),
            "http://127.0.0.1:54321/auth/v1/token?grant_type=password"
        );
    }
}
'''
if "mod endpoint_tests" in text:
    raise SystemExit("endpoint regression tests unexpectedly exist at the locked source head")
handoff.write_text(text.rstrip() + regression + "\n")

crypto = Path("src/handoff/crypto.rs")
replace_once(
    crypto,
    "use hmac::{Hmac, Mac};\nuse rand::RngCore;",
    "use hmac::{Hmac, KeyInit as HmacKeyInit, Mac};\nuse rand::Rng;",
    "rand/HMAC imports",
)
replace_once(
    crypto,
    '''        let mut nonce = [0_u8; 12];
        rand::rng().fill_bytes(&mut nonce);
        let ciphertext = self
            .0
            .encrypt(
                Nonce::from_slice(&nonce),
                Payload {
                    msg: &plaintext,
                    aad,
                },
            )
            .map_err(|_| AuthError::Internal)?;
        let mut encoded = Vec::with_capacity(nonce.len() + ciphertext.len());
        encoded.extend_from_slice(&nonce);''',
    '''        let mut nonce_bytes = [0_u8; 12];
        rand::rng().fill(&mut nonce_bytes);
        let nonce = Nonce::from(nonce_bytes);
        let ciphertext = self
            .0
            .encrypt(
                &nonce,
                Payload {
                    msg: &plaintext,
                    aad,
                },
            )
            .map_err(|_| AuthError::Internal)?;
        let mut encoded = Vec::with_capacity(nonce_bytes.len() + ciphertext.len());
        encoded.extend_from_slice(&nonce_bytes);''',
    "AES-GCM encryption nonce",
)
replace_once(
    crypto,
    '''        let (nonce, ciphertext) = encoded.split_at(12);
        let plaintext = self
            .0
            .decrypt(
                Nonce::from_slice(nonce),
                Payload {
                    msg: ciphertext,
                    aad,
                },
            )''',
    '''        let (nonce_bytes, ciphertext) = encoded.split_at(12);
        let nonce_bytes: [u8; 12] = nonce_bytes.try_into().map_err(|_| AuthError::Internal)?;
        let nonce = Nonce::from(nonce_bytes);
        let plaintext = self
            .0
            .decrypt(
                &nonce,
                Payload {
                    msg: ciphertext,
                    aad,
                },
            )''',
    "AES-GCM decryption nonce",
)
replace_once(
    crypto,
    "rand::rng().fill_bytes(&mut bytes);",
    "rand::rng().fill(&mut bytes);",
    "authorization-code randomness",
)
replace_once(
    crypto,
    "<HmacSha256 as Mac>::new_from_slice(SECRET_COMPARISON_KEY)",
    "<HmacSha256 as HmacKeyInit>::new_from_slice(SECRET_COMPARISON_KEY)",
    "first HMAC constructor",
)
# The source contains the constructor twice. The first replacement above is
# intentionally exact-once per call; repair the remaining constructor now.
replace_once(
    crypto,
    "<HmacSha256 as Mac>::new_from_slice(SECRET_COMPARISON_KEY)",
    "<HmacSha256 as HmacKeyInit>::new_from_slice(SECRET_COMPARISON_KEY)",
    "second HMAC constructor",
)

http = Path("src/http/handoff.rs")
replace_once(
    http,
    "use axum_extra::extract::cookie::{time::Duration as CookieDuration, Cookie, CookieJar, SameSite};",
    "use axum_extra::extract::cookie::{Cookie, CookieJar, SameSite};\nuse time::Duration as CookieDuration;",
    "cookie duration import",
)
