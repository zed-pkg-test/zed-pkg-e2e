#pragma once
// Restricted native Atto scalar parser: one main() with one return expression.
#include "atto/scalar_lexer.hpp"
#include "atto/decimal_i64.hpp"
#include <cstdint>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace atto::forge::scalar {
enum class Op { literal, add, sub, mul, div, rem, neg, band, bor, bxor, bnot, shl, lshr, ashr };
struct Node {
  Op op;
  std::size_t left = 0, right = 0;
  i64 value = 0;
};
enum class ParseError {
  none, lexical, ownership_bang_unsupported, syntax, literal_out_of_range, depth_limit, node_limit,
  pointer_profile
};
// Stable sanitized diagnostics; never echo user source bytes.
[[nodiscard]] constexpr std::string_view parse_error_name(ParseError error) noexcept {
  switch (error) {
    case ParseError::none: return "NONE";
    case ParseError::lexical: return "E_PARSE_LEXICAL";
    case ParseError::ownership_bang_unsupported: return "E_OWNERSHIP_BANG_UNSUPPORTED";
    case ParseError::syntax: return "E_PARSE_SYNTAX";
    case ParseError::literal_out_of_range: return "E_PARSE_RANGE";
    case ParseError::depth_limit: return "E_PARSE_DEPTH";
    case ParseError::node_limit: return "E_PARSE_NODES";
    case ParseError::pointer_profile: return "E_POINTER_PROFILE";
  }
  return "E_PARSE_UNKNOWN";
}

// BorrowedLocal is NOT an owning Pointer.alloc<T> region or a raw Atto
// pointer. The canonical parser verifies the exact lexical sequence and
// erases all source names before emission. Only a single initialized i64
// place with a compiler-authenticated readonly handle is admitted.
struct BorrowedLocal {
  std::string referent;
  std::string handle;
  std::string copied_scalar;
  i64 initial = 0;
};
struct Parsed {
  std::optional<BorrowedLocal> local;
  std::vector<Node> nodes;
  std::size_t root = 0;
  ParseError error = ParseError::none;
  std::size_t error_offset = 0;
  [[nodiscard]] bool ok() const noexcept {
    return error == ParseError::none && !nodes.empty() && root < nodes.size();
  }
};
inline constexpr std::size_t max_nodes = 128;
inline constexpr int max_parse_depth = 24;

// Verify all untrusted or externally constructed ASTs before evaluation or
// code emission. A valid postorder AST has the root as its final node,
// backward-only edges, no detached nodes, and recognized operation tags.
[[nodiscard]] inline bool validate_ast(const Parsed& p) {
  if (!p.ok() || p.nodes.size() > max_nodes ||
      p.root != p.nodes.size() - 1) return false;
  if (p.local) {
    // A forged or mutated typed plan must not be allowed to change its
    // value, launder a raw address, or become an owning pointer. There is
    // exactly one canonical postorder i64 read-copy result.
    const auto& l = *p.local;
    const auto safe_name = [](const std::string& n) {
      if (n.empty() || n.size() > 96) return false;
      auto start = [](char ch) {
        return (ch >= 'a' && ch <= 'z') ||
               (ch >= 'A' && ch <= 'Z') || ch == '_';
      };
      if (!start(n[0])) return false;
      for (char ch : n)
        if (!start(ch) && !(ch >= '0' && ch <= '9')) return false;
      return true;
    };
    const auto reserved = [](const std::string& n) {
      return n == "main" || n == "int" || n == "Pointer" ||
             n == "let" || n == "val" || n == "rt" ||
             n == "ref" || n == "deref" || n == "unref" ||
             n == "return" || n == "fnc" || n == "pub";
    };
    if (!safe_name(l.referent) || !safe_name(l.handle) ||
        !safe_name(l.copied_scalar) ||
        reserved(l.referent) || reserved(l.handle) ||
        reserved(l.copied_scalar) ||
        l.referent == l.handle || l.referent == l.copied_scalar ||
        l.handle == l.copied_scalar ||
        p.nodes.size() != 1 || p.root != 0 ||
        p.nodes[0].op != Op::literal || p.nodes[0].value != l.initial)
      return false;
  }
  std::vector<unsigned char> reachable(p.nodes.size(), 0);
  std::vector<std::size_t> pending{p.root};
  while (!pending.empty()) {
    const auto index = pending.back();
    pending.pop_back();
    if (index >= p.nodes.size()) return false;
    if (reachable[index]) continue;
    reachable[index] = 1;
    const auto& node = p.nodes[index];
    switch (node.op) {
      case Op::literal:
        if (node.left != 0 || node.right != 0) return false;
        break;
      case Op::neg:
      case Op::bnot:
        if (node.left >= index || node.right != 0) return false;
        pending.push_back(node.left);
        break;
      case Op::add:
      case Op::sub:
      case Op::mul:
      case Op::div:
      case Op::rem:
      case Op::band:
      case Op::bor:
      case Op::bxor:
      case Op::shl:
      case Op::lshr:
      case Op::ashr:
        if (node.left >= index || node.right >= index) return false;
        pending.push_back(node.left);
        pending.push_back(node.right);
        break;
      default: return false;
    }
  }
  for (auto seen : reachable) if (!seen) return false;
  return true;
}

class Parser {
  std::string_view source_;
  std::vector<Token> tokens_;
  std::size_t pos_ = 0;
  Parsed out_;
  [[nodiscard]] const Token& current() const { return tokens_[pos_]; }
  [[nodiscard]] std::string_view text() const {
    const auto& t = current();
    return source_.substr(t.offset, t.length);
  }
  void fail(ParseError error) {
    if (out_.error == ParseError::none) {
      out_.error = error;
      out_.error_offset = current().offset;
    }
  }
  [[nodiscard]] bool take(Kind kind) {
    if (current().kind != kind) return false;
    ++pos_;
    return true;
  }
  [[nodiscard]] bool name(std::string_view word) {
    if (current().kind != Kind::name || text() != word) return false;
    ++pos_;
    return true;
  }
  // Every expression in this restricted scalar frontend evaluates to i64;
  // infer therefore resolves to i64 without permitting dynamic values.
  [[nodiscard]] bool scalar_return_type() {
    return name("int") || name("infer");
  }
  [[nodiscard]] std::optional<std::size_t> append(Node n) {
    if (out_.nodes.size() >= max_nodes) {
      fail(ParseError::node_limit);
      return std::nullopt;
    }
    out_.nodes.push_back(n);
    return out_.nodes.size() - 1;
  }
  // bit_or < bit_xor < bit_and < addition < multiplication < unary.
  // Flat precedence tiers do not consume the nesting depth budget.
  // Only actual parenthesized or unary nesting increments depth.
  [[nodiscard]] std::optional<std::size_t> expr(int depth) {
    return parse_or(depth);
  }
  [[nodiscard]] std::optional<std::size_t> parse_or(int depth) {
    auto lhs = parse_xor(depth);
    if (!lhs) return std::nullopt;
    while (take(Kind::bit_or)) {
      auto rhs = parse_xor(depth);
      if (!rhs) return std::nullopt;
      lhs = append(Node{Op::bor, *lhs, *rhs, 0});
      if (!lhs) return std::nullopt;
    }
    return lhs;
  }
  [[nodiscard]] std::optional<std::size_t> parse_xor(int depth) {
    auto lhs = parse_and(depth);
    if (!lhs) return std::nullopt;
    while (take(Kind::bit_xor)) {
      auto rhs = parse_and(depth);
      if (!rhs) return std::nullopt;
      lhs = append(Node{Op::bxor, *lhs, *rhs, 0});
      if (!lhs) return std::nullopt;
    }
    return lhs;
  }
  [[nodiscard]] std::optional<std::size_t> parse_and(int depth) {
    auto lhs = parse_shift(depth);
    if (!lhs) return std::nullopt;
    while (take(Kind::bit_and)) {
      auto rhs = parse_shift(depth);
      if (!rhs) return std::nullopt;
      lhs = append(Node{Op::band, *lhs, *rhs, 0});
      if (!lhs) return std::nullopt;
    }
    return lhs;
  }
  [[nodiscard]] std::optional<std::size_t> parse_shift(int depth) {
    auto lhs = additive(depth);
    if (!lhs) return std::nullopt;
    while (current().kind == Kind::shl || current().kind == Kind::lshr ||
           (current().kind == Kind::name && text() == "ashr")) {
      const auto op = current().kind == Kind::shl ? Op::shl :
                      current().kind == Kind::lshr ? Op::lshr : Op::ashr;
      ++pos_;
      auto rhs = additive(depth);
      if (!rhs) return std::nullopt;
      lhs = append(Node{op, *lhs, *rhs, 0});
      if (!lhs) return std::nullopt;
    }
    return lhs;
  }
  [[nodiscard]] std::optional<std::size_t> additive(int depth) {
    if (depth > max_parse_depth) { fail(ParseError::depth_limit); return std::nullopt; }
    auto lhs = term(depth);
    if (!lhs) return std::nullopt;
    while (current().kind == Kind::plus || current().kind == Kind::minus) {
      const auto op = current().kind == Kind::plus ? Op::add : Op::sub;
      ++pos_;
      auto rhs = term(depth);
      if (!rhs) return std::nullopt;
      lhs = append(Node{op, *lhs, *rhs, 0});
      if (!lhs) return std::nullopt;
    }
    return lhs;
  }
  [[nodiscard]] std::optional<std::size_t> term(int depth) {
    if (depth > max_parse_depth) { fail(ParseError::depth_limit); return std::nullopt; }
    auto lhs = unary(depth);
    if (!lhs) return std::nullopt;
    while (current().kind == Kind::star || current().kind == Kind::slash ||
           current().kind == Kind::percent) {
      Op op = Op::mul;
      if (current().kind == Kind::slash) op = Op::div;
      if (current().kind == Kind::percent) op = Op::rem;
      ++pos_;
      auto rhs = unary(depth);
      if (!rhs) return std::nullopt;
      lhs = append(Node{op, *lhs, *rhs, 0});
      if (!lhs) return std::nullopt;
    }
    return lhs;
  }
  [[nodiscard]] std::optional<std::size_t> unary(int depth) {
    if (depth > max_parse_depth) { fail(ParseError::depth_limit); return std::nullopt; }
    if (take(Kind::plus)) return unary(depth + 1);
    if (take(Kind::tilde)) {
      auto rhs = unary(depth + 1);
      if (!rhs) return std::nullopt;
      return append(Node{Op::bnot, *rhs, 0, 0});
    }
    if (take(Kind::minus)) {
      // Literal INT64_MIN is allowed; negating any evaluated INT64_MIN
      // is a checked overflow in the subsequent evaluator/codegen.
      if (current().kind == Kind::integer && text().size() < 32) {
        // The negative range includes |INT64_MIN|. Reuse the canonical
        // decimal validator instead of comparing only the unpadded token.
        // This accepts -0009223372036854775808 consistently with other
        // decimal leading-zero literals but rejects larger magnitudes.
        const std::string signed_digits = "-" + std::string(text());
        const auto minimum = parse_decimal_i64(signed_digits);
        if (minimum && *minimum == std::numeric_limits<i64>::min()) {
          ++pos_;
          return append(Node{Op::literal, 0, 0, *minimum});
        }
      }
      auto rhs = unary(depth + 1);
      if (!rhs) return std::nullopt;
      return append(Node{Op::neg, *rhs, 0, 0});
    }
    if (take(Kind::lparen)) {
      auto inside = expr(depth + 1);
      if (!inside) return std::nullopt;
      if (!take(Kind::rparen)) { fail(ParseError::syntax); return std::nullopt; }
      return inside;
    }
    if (current().kind == Kind::integer) {
      auto n = parse_decimal_i64(text());
      if (!n) { fail(ParseError::literal_out_of_range); return std::nullopt; }
      ++pos_;
      return append(Node{Op::literal, 0, 0, *n});
    }
    fail(ParseError::syntax);
    return std::nullopt;
  }
  [[nodiscard]] bool expected(Kind kind) {
    if (take(kind)) return true;
    fail(ParseError::pointer_profile);
    return false;
  }
  [[nodiscard]] bool expected(std::string_view word) {
    if (name(word)) return true;
    fail(ParseError::pointer_profile);
    return false;
  }
  [[nodiscard]] std::optional<std::string> local_id() {
    if (current().kind != Kind::name) {
      fail(ParseError::pointer_profile);
      return std::nullopt;
    }
    std::string name_value(text());
    ++pos_;
    if (name_value.size() > 96 ||
        name_value == "main" || name_value == "int" ||
        name_value == "Pointer" || name_value == "let" ||
        name_value == "val" || name_value == "rt" ||
        name_value == "ref" || name_value == "deref" ||
        name_value == "unref" || name_value == "return" ||
        name_value == "fnc" || name_value == "pub") {
      fail(ParseError::pointer_profile);
      return std::nullopt;
    }
    return name_value;
  }
  [[nodiscard]] bool local_op(std::string_view op) {
    if (name("rt")) return expected(op);
    const auto begin = current().offset;
    if (!expected(op)) return false;
    // Even whitespace or comments between operation and ! are rejected;
    // source offsets are retained by the *canonical* lexer.
    if (current().kind != Kind::bang ||
        current().offset != begin + op.size()) {
      fail(ParseError::pointer_profile);
      return false;
    }
    ++pos_;
    return true;
  }
  [[nodiscard]] bool local_pointer() {
    auto owner = local_id();
    if (!owner || !expected(Kind::colon) ||
        !expected("int") || !expected(Kind::assign))
      return false;
    if (current().kind != Kind::integer) {
      fail(ParseError::pointer_profile);
      return false;
    }
    auto initial = parse_decimal_i64(text());
    if (!initial) {
      fail(ParseError::literal_out_of_range);
      return false;
    }
    ++pos_;
    if (!expected(Kind::semicolon) || !expected("val") ||
        !expected("Pointer") || !expected(Kind::lt) ||
        !expected("int") || !expected(Kind::gt))
      return false;
    auto handle = local_id();
    if (!handle) return false;
    if (*handle == *owner) {
      fail(ParseError::pointer_profile);
      return false;
    }
    if (!expected(Kind::assign) || !local_op("ref") ||
        !expected(*owner) || !expected(Kind::semicolon) ||
        !expected("val"))
      return false;
    auto copy = local_id();
    if (!copy) return false;
    if (*copy == *owner || *copy == *handle) {
      fail(ParseError::pointer_profile);
      return false;
    }
    if (!expected(Kind::assign) || !local_op("deref") ||
        !expected(*handle) || !expected(Kind::semicolon) ||
        !local_op("unref") || !expected(*handle) ||
        !expected(Kind::semicolon) || !expected("return") ||
        !expected(*copy) || !expected(Kind::semicolon) ||
        !expected(Kind::rbrace) || current().kind != Kind::end) {
      fail(ParseError::pointer_profile);
      return false;
    }
    // The proof is closed-world: exactly one readonly copy and one unref
    // precede the only return. No branch, call, actor, await, handle copy,
    // second function or operation is accepted before guest emission.
    out_.local = BorrowedLocal{*owner, *handle, *copy, *initial};
    auto root = append(Node{Op::literal, 0, 0, *initial});
    if (!root) return false;
    out_.root = *root;
    return true;
  }
public:
  Parser(std::string_view source, Lexed lexed)
      : source_(source), tokens_(std::move(lexed.tokens)) {
    if (!lexed.ok()) {
      out_.error = lexed.error == LexError::ownership_bang_unsupported
          ? ParseError::ownership_bang_unsupported : ParseError::lexical;
      out_.error_offset = lexed.error_offset;
    }
  }
  [[nodiscard]] Parsed parse() {
    if (out_.error != ParseError::none) return std::move(out_);
    (void)name("pub");
    const bool fnc = name("fnc");
    bool signature_ok = false;
    if (scalar_return_type()) {
      // Prefix int/infer return type, with or without fnc.
      signature_ok = take(Kind::left_arrow) && name("main") &&
          take(Kind::lparen) && take(Kind::rparen);
    } else if (fnc && name("main") && take(Kind::lparen) &&
               take(Kind::rparen)) {
      // Accept colon return type, arrow return type, or block arrow.
      if (take(Kind::colon)) {
        signature_ok = scalar_return_type();
        if (signature_ok) (void)take(Kind::arrow);
      } else if (take(Kind::arrow)) {
        signature_ok = scalar_return_type();
      }
    }
    if (!signature_ok || !take(Kind::lbrace)) {
      fail(ParseError::syntax);
      return std::move(out_);
    }
    // An explicit let header opts into the bounded local-place authority
    // profile; if it fails, never reinterpret it as an ordinary expression.
    if (name("let")) {
      (void)local_pointer();
      return std::move(out_);
    }
    if (!name("return")) {
      fail(ParseError::syntax);
      return std::move(out_);
    }
    auto root = expr(0);
    if (!root) return std::move(out_);
    if (!take(Kind::semicolon) || !take(Kind::rbrace) ||
        current().kind != Kind::end) {
      fail(ParseError::syntax);
      return std::move(out_);
    }
    out_.root = *root;
    return std::move(out_);
  }
};

[[nodiscard]] inline Parsed parse_source(std::string_view src) {
  return Parser(src, tokenize(src)).parse();
}
}  // namespace atto::forge::scalar
