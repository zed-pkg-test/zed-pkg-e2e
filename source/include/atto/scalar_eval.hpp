#pragma once
// Restricted scalar AST evaluation with explicit arithmetic traps.
// Host-model verification only: no executable .atto guest generated here.
#include "atto/scalar_parser.hpp"
#include <optional>
#include <string_view>
#include <vector>

namespace atto::forge::scalar {
enum class EvalError { none, parse_error, invalid_ast, overflow, divide_by_zero };
struct Evaluated {
  std::optional<i64> value;
  EvalError error = EvalError::none;
  [[nodiscard]] bool ok() const noexcept {
    return error == EvalError::none && value.has_value();
  }
};
[[nodiscard]] inline Evaluated evaluate(const Parsed& p) {
  if (!validate_ast(p)) return {{}, EvalError::invalid_ast};
  std::vector<i64> values;
  values.reserve(p.nodes.size());
  for (std::size_t i = 0; i < p.nodes.size(); ++i) {
    const Node& node = p.nodes[i];
    if (node.op == Op::literal) {
      values.push_back(node.value);
      continue;
    }
    if (node.left >= i || (node.op != Op::neg && node.op != Op::bnot && node.right >= i))
      return {{}, EvalError::invalid_ast};
    const i64 lhs = values[node.left];
    std::optional<i64> result;
    switch (node.op) {
      case Op::bnot:
        result = bit_not(lhs);
        break;
      case Op::neg:
        result = checked_neg(lhs);
        break;
      case Op::add:
        result = checked_add(lhs, values[node.right]);
        break;
      case Op::sub:
        result = checked_sub(lhs, values[node.right]);
        break;
      case Op::mul:
        result = checked_mul(lhs, values[node.right]);
        break;
      case Op::div:
        if (values[node.right] == 0) return {{}, EvalError::divide_by_zero};
        result = checked_div(lhs, values[node.right]);
        break;
      case Op::shl:
        result = shl_i64(lhs, values[node.right]);
        break;
      case Op::lshr:
        result = lshr_i64(lhs, values[node.right]);
        break;
      case Op::ashr:
        result = ashr_i64(lhs, values[node.right]);
        break;
      case Op::band:
        result = bit_and(lhs, values[node.right]);
        break;
      case Op::bor:
        result = bit_or(lhs, values[node.right]);
        break;
      case Op::bxor:
        result = bit_xor(lhs, values[node.right]);
        break;
      case Op::rem:
        if (values[node.right] == 0) return {{}, EvalError::divide_by_zero};
        result = checked_rem(lhs, values[node.right]);
        break;
      default:
        return {{}, EvalError::invalid_ast};
    }
    if (!result) return {{}, EvalError::overflow};
    values.push_back(*result);
  }
  return {values[p.root], EvalError::none};
}
[[nodiscard]] inline Evaluated evaluate_source(std::string_view src) {
  const auto p = parse_source(src);
  if (!p.ok()) return {{}, EvalError::parse_error};
  return evaluate(p);
}
}  // namespace atto::forge::scalar
