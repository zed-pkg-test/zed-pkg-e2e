#pragma once
// Stage-0 restricted .atto scalar lexer. No Python, no guest execution claim.
// Token locations are offsets, never borrowed views into a temporary source.
#include <cstddef>
#include <string_view>
#include <vector>

namespace atto::forge::scalar {
enum class Kind {
  name, integer, lparen, rparen, lbrace, rbrace, colon, semicolon,
  plus, minus, star, slash, percent, bit_and, bit_or, bit_xor, tilde, shl, lshr, arrow, left_arrow,
  assign, lt, gt, bang, end
};
struct Token { Kind kind; std::size_t offset; std::size_t length; };
enum class LexError { none, source_too_large, token_limit, unsupported_byte, unterminated_comment };
struct Lexed {
  std::vector<Token> tokens;
  LexError error = LexError::none;
  std::size_t error_offset = 0;
  [[nodiscard]] bool ok() const noexcept { return error == LexError::none; }
};
inline constexpr std::size_t max_source_bytes = 16 * 1024;
inline constexpr std::size_t max_tokens = 256;

[[nodiscard]] inline Lexed tokenize(std::string_view src) {
  Lexed out;
  if (src.size() > max_source_bytes) {
    out.error = LexError::source_too_large;
    out.error_offset = max_source_bytes;
    return out;
  }
  auto is_name_start = [](char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_';
  };
  auto is_digit = [](char c) { return c >= '0' && c <= '9'; };
  auto is_name = [&](char c) { return is_name_start(c) || is_digit(c); };
  std::size_t i = 0;
  while (i < src.size()) {
    const char c = src[i];
    if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
      ++i;
      continue;
    }
    // Skip comments before counting tokens: even a full-size comment-only
    // file has no tokens beyond the EOF sentinel.
    if (c == '/' && i + 1 < src.size() && src[i + 1] == '/') {
      i += 2;
      while (i < src.size() && src[i] != '\r' && src[i] != '\n') ++i;
      continue;
    }
    if (c == '/' && i + 1 < src.size() && src[i + 1] == '*') {
      const std::size_t opening = i;
      i += 2;
      bool closed = false;
      while (i + 1 < src.size()) {
        if (src[i] == '*' && src[i + 1] == '/') {
          i += 2;
          closed = true;
          break;
        }
        ++i;
      }
      if (!closed) {
        out.error = LexError::unterminated_comment;
        out.error_offset = opening;
        return out;
      }
      continue;
    }
    if (out.tokens.size() >= max_tokens) {
      out.error = LexError::token_limit;
      out.error_offset = i;
      return out;
    }
    const std::size_t start = i;
    Kind kind;
    if (is_name_start(c)) {
      kind = Kind::name;
      do { ++i; } while (i < src.size() && is_name(src[i]));
    } else if (c == '-' && src.substr(i, 2) == "->") {
      kind = Kind::arrow;
      i += 2;
    } else if (c == '<' && src.substr(i, 2) == "<-") {
      kind = Kind::left_arrow;
      i += 2;
    } else if (c == '<' && src.substr(i, 3) == "<<<") {
      kind = Kind::shl;
      i += 3;
    } else if (c == '>' && src.substr(i, 3) == ">>>") {
      kind = Kind::lshr;
      i += 3;
    } else if (is_digit(c)) {
      kind = Kind::integer;
      do { ++i; } while (i < src.size() && is_digit(src[i]));
    } else {
      ++i;
      switch (c) {
        case '(': kind = Kind::lparen; break;
        case ')': kind = Kind::rparen; break;
        case '{': kind = Kind::lbrace; break;
        case '}': kind = Kind::rbrace; break;
        case ':': kind = Kind::colon; break;
        case ';': kind = Kind::semicolon; break;
        case '+': kind = Kind::plus; break;
        case '-': kind = Kind::minus; break;
        case '*': kind = Kind::star; break;
        case '/': kind = Kind::slash; break;
        case '%': kind = Kind::percent; break;
        case '&': kind = Kind::bit_and; break;
        case '|': kind = Kind::bit_or; break;
        case '^': kind = Kind::bit_xor; break;
        case '~': kind = Kind::tilde; break;
        case '=': kind = Kind::assign; break;
        case '<': kind = Kind::lt; break;
        case '>': kind = Kind::gt; break;
        case '!': kind = Kind::bang; break;
        default:
          out.error = LexError::unsupported_byte;
          out.error_offset = start;
          return out;
      }
    }
    out.tokens.push_back(Token{kind, start, i - start});
  }
  out.tokens.push_back(Token{Kind::end, src.size(), 0});
  return out;
}
}  // namespace atto::forge::scalar
