#pragma once

// Portable C++20 checked i64 runtime primitives for Forge experiments.
// These are NOT a .atto compiler or actor runtime.
#include <bit>
#include <cstdint>
#include <limits>
#include <optional>

namespace atto::forge {
using i64 = std::int64_t;
using u64 = std::uint64_t;

[[nodiscard]] constexpr std::optional<i64> checked_add(i64 a, i64 b) noexcept {
  constexpr i64 lo = std::numeric_limits<i64>::min();
  constexpr i64 hi = std::numeric_limits<i64>::max();
  if ((b > 0 && a > hi - b) || (b < 0 && a < lo - b))
    return std::nullopt;
  return a + b;
}

[[nodiscard]] constexpr std::optional<i64> checked_sub(i64 a, i64 b) noexcept {
  constexpr i64 lo = std::numeric_limits<i64>::min();
  constexpr i64 hi = std::numeric_limits<i64>::max();
  if ((b > 0 && a < lo + b) || (b < 0 && a > hi + b))
    return std::nullopt;
  return a - b;
}

[[nodiscard]] constexpr u64 magnitude(i64 value) noexcept {
  const u64 bits = static_cast<u64>(value);
  return value < 0 ? (~bits + u64{1}) : bits;
}

[[nodiscard]] constexpr std::optional<i64> checked_mul(i64 a, i64 b) noexcept {
  constexpr i64 lo = std::numeric_limits<i64>::min();
  constexpr u64 positive_limit = static_cast<u64>(std::numeric_limits<i64>::max());
  constexpr u64 negative_limit = positive_limit + u64{1};
  const bool negative = (a < 0) != (b < 0);
  const u64 left = magnitude(a), right = magnitude(b);
  const u64 limit = negative ? negative_limit : positive_limit;
  if (right != 0 && left > limit / right)
    return std::nullopt;
  const u64 product = left * right; // guaranteed <= 2^63
  if (negative && product == negative_limit)
    return lo;
  const i64 value = static_cast<i64>(product); // guaranteed <= INT64_MAX
  return negative ? -value : value;
}
// Truncation toward zero, matching C++ signed division semantics.
// Both division by zero and INT64_MIN / -1 are rejected before evaluation.
[[nodiscard]] constexpr std::optional<i64> checked_div(i64 a, i64 b) noexcept {
  if (b == 0 || (a == std::numeric_limits<i64>::min() && b == -1))
    return std::nullopt;
  return a / b;
}

[[nodiscard]] constexpr std::optional<i64> checked_rem(i64 a, i64 b) noexcept {
  if (b == 0 || (a == std::numeric_limits<i64>::min() && b == -1))
    return std::nullopt;
  return a % b;
}
// Both operations must reject INT64_MIN to avoid signed overflow.
[[nodiscard]] constexpr std::optional<i64> checked_neg(i64 a) noexcept {
  if (a == std::numeric_limits<i64>::min()) return std::nullopt;
  return -a;
}

[[nodiscard]] constexpr std::optional<i64> checked_abs(i64 a) noexcept {
  if (a == std::numeric_limits<i64>::min()) return std::nullopt;
  return a < 0 ? -a : a;
}
// Preserve exact 64-bit two's-complement bit patterns on all C++20 targets.
// std::bit_cast avoids implementation-defined unsigned-to-signed conversion.
static_assert(sizeof(i64) == sizeof(u64));
[[nodiscard]] constexpr std::optional<i64> bit_and(i64 a, i64 b) noexcept {
  return std::bit_cast<i64>(static_cast<u64>(a) & static_cast<u64>(b));
}
[[nodiscard]] constexpr std::optional<i64> bit_or(i64 a, i64 b) noexcept {
  return std::bit_cast<i64>(static_cast<u64>(a) | static_cast<u64>(b));
}
[[nodiscard]] constexpr std::optional<i64> bit_xor(i64 a, i64 b) noexcept {
  return std::bit_cast<i64>(static_cast<u64>(a) ^ static_cast<u64>(b));
}
[[nodiscard]] constexpr std::optional<i64> bit_not(i64 a) noexcept {
  return std::bit_cast<i64>(~static_cast<u64>(a));
}
// Signed counts mask to the low six bits (modulo 64).
// Unsigned shifts avoid undefined signed-left-shift and implementation-defined
// signed right shift. Arithmetic right shift extends the sign explicitly.
[[nodiscard]] constexpr std::optional<i64> shl_i64(i64 value, i64 count) noexcept {
  const unsigned shift = static_cast<unsigned>(static_cast<u64>(count) & 63);
  return std::bit_cast<i64>(static_cast<u64>(value) << shift);
}
[[nodiscard]] constexpr std::optional<i64> lshr_i64(i64 value, i64 count) noexcept {
  const unsigned shift = static_cast<unsigned>(static_cast<u64>(count) & 63);
  return std::bit_cast<i64>(static_cast<u64>(value) >> shift);
}
[[nodiscard]] constexpr std::optional<i64> ashr_i64(i64 value, i64 count) noexcept {
  const unsigned shift = static_cast<unsigned>(static_cast<u64>(count) & 63);
  if (shift == 0) return value;
  u64 bits = static_cast<u64>(value) >> shift;
  if (value < 0) bits |= ~u64{0} << (64U - shift);
  return std::bit_cast<i64>(bits);
}
}  // namespace atto::forge
