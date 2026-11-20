#pragma once
// Deterministic C++20 source emission from a validated restricted .atto AST.
// Source bytes are never concatenated into output: only checked AST data.
#include "atto/scalar_parser.hpp"
#include <limits>
#include <optional>
#include <string>

namespace atto::forge::scalar {
inline constexpr std::size_t max_emitted_bytes = 64 * 1024;
[[nodiscard]] inline std::optional<std::string> emit_cpp(const Parsed& p) {
  if (!validate_ast(p)) return std::nullopt;
  std::string code = R"(#include <atto/checked_i64.hpp>
#include <cstdio>
#include <cstdint>
#include <limits>
#include <optional>
int main() {
)";
  // Emit genuine native stack storage and a const pointer read. Names from
  // user source never enter generated C++; the borrow is compiler-authenticated
  // and nonowning, and unref only invalidates this lexical handle.
  if (p.local) {
    const auto initial = std::to_string(p.local->initial);
    code += "  const atto::forge::i64 __atto_place{" + initial + "};\n";
    code += "  const atto::forge::i64* __atto_borrow = &__atto_place;\n";
    code += "  const atto::forge::i64 __atto_copy = *__atto_borrow;\n";
    code += "  __atto_borrow = nullptr; // non-owning unref, not delete/free\n";
    code += "  std::printf(\"%lld\\n\", static_cast<long long>(__atto_copy));\n";
    code += "  return 0;\n}\n";
    if (code.size() > max_emitted_bytes) return std::nullopt;
    return code;
  }
  auto var = [](std::size_t n) { return "v" + std::to_string(n); };
  for (std::size_t i = 0; i < p.nodes.size(); ++i) {
    const auto& n = p.nodes[i];
    if (n.op == Op::literal) {
      const auto value = n.value == std::numeric_limits<i64>::min()
          ? "std::numeric_limits<atto::forge::i64>::min()"
          : std::to_string(n.value);
      code += "  std::optional<atto::forge::i64> " + var(i) + "{" + value + "};\n";
    } else {
      if (n.left >= i || (n.op != Op::neg && n.op != Op::bnot && n.right >= i)) return std::nullopt;
      std::string call;
      switch (n.op) {
        case Op::bnot:
          call = "bit_not(*" + var(n.left) + ")";
          break;
        case Op::neg:
          call = "checked_neg(*" + var(n.left) + ")";
          break;
        case Op::add:
          call = "checked_add(*" + var(n.left) + ", *" + var(n.right) + ")";
          break;
        case Op::sub:
          call = "checked_sub(*" + var(n.left) + ", *" + var(n.right) + ")";
          break;
        case Op::shl:
          call = "shl_i64(*" + var(n.left) + ", *" + var(n.right) + ")";
          break;
        case Op::lshr:
          call = "lshr_i64(*" + var(n.left) + ", *" + var(n.right) + ")";
          break;
        case Op::ashr:
          call = "ashr_i64(*" + var(n.left) + ", *" + var(n.right) + ")";
          break;
        case Op::band:
          call = "bit_and(*" + var(n.left) + ", *" + var(n.right) + ")";
          break;
        case Op::bor:
          call = "bit_or(*" + var(n.left) + ", *" + var(n.right) + ")";
          break;
        case Op::bxor:
          call = "bit_xor(*" + var(n.left) + ", *" + var(n.right) + ")";
          break;
        case Op::mul:
          call = "checked_mul(*" + var(n.left) + ", *" + var(n.right) + ")";
          break;
        case Op::div:
        case Op::rem:
          code += "  if (*" + var(n.right) +
              " == 0) { std::fputs(\"E_DIVZERO\\n\", stderr); return 71; }\n";
          call = (n.op == Op::div ? "checked_div(*" : "checked_rem(*") +
              var(n.left) + ", *" + var(n.right) + ")";
          break;
        default:
          return std::nullopt;
      }
      code += "  auto " + var(i) + " = atto::forge::" + call + ";\n";
      code += "  if (!" + var(i) +
          ") { std::fputs(\"E_OVERFLOW\\n\", stderr); return 70; }\n";
    }
    if (code.size() > max_emitted_bytes) return std::nullopt;
  }
  code += "  std::printf(\"%lld\\n\", static_cast<long long>(*" + var(p.root) +
          "));\n  return 0;\n}\n";
  if (code.size() > max_emitted_bytes) return std::nullopt;
  return code;
}
[[nodiscard]] inline std::optional<std::string> emit_cpp_source(
    std::string_view source) {
  const auto parsed = parse_source(source);
  if (!parsed.ok()) return std::nullopt;
  return emit_cpp(parsed);
}
}  // namespace atto::forge::scalar
