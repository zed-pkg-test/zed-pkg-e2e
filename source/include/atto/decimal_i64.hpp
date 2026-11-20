#pragma once
// Bounded, allocation-free decimal token conversion for the native Forge host.
// This is not a complete .atto lexer, AST, or source-language grammar.
#include "atto/checked_i64.hpp"
#include <string_view>

namespace atto::forge {
[[nodiscard]] constexpr std::optional<i64> parse_decimal_i64(
    std::string_view text) noexcept {
  // Fail closed on arbitrarily long untrusted tokens, including leading zeroes.
  if (text.empty() || text.size() > 32) return std::nullopt;
  bool negative = false;
  std::size_t index = 0;
  if (text.front() == '-' || text.front() == '+') {
    negative = text.front() == '-';
    index = 1;
  }
  if (index == text.size()) return std::nullopt;
  constexpr u64 positive_limit = static_cast<u64>(
      std::numeric_limits<i64>::max());
  constexpr u64 negative_limit = positive_limit + u64{1};
  const u64 limit = negative ? negative_limit : positive_limit;
  u64 value = 0;
  for (; index < text.size(); ++index) {
    const char c = text[index];
    if (c < '0' || c > '9') return std::nullopt;
    const u64 digit = static_cast<u64>(c - '0');
    if (value > (limit - digit) / u64{10}) return std::nullopt;
    value = value * u64{10} + digit;
  }
  if (negative && value == negative_limit)
    return std::numeric_limits<i64>::min();
  const i64 signed_value = static_cast<i64>(value); // <= INT64_MAX
  return negative ? -signed_value : signed_value;
}
}  // namespace atto::forge
