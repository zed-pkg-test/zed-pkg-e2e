// Independent built-in compiler overflow oracles for native i64 primitives.
// Reproducible seed; GCC and Clang only, ASan/UBSan enabled by CMake.
#include "atto/checked_i64.hpp"
#include <array>
#include <bit>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <optional>
using atto::forge::i64;
using atto::forge::u64;
using namespace atto::forge;
static void require(bool ok) { if (!ok) std::abort(); }

struct Splitmix64 {
  u64 state = 0x6a09e667f3bcc909ULL;
  [[nodiscard]] u64 next() {
    u64 z = (state += 0x9e3779b97f4a7c15ULL);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
  }
};

static void check(i64 a, i64 b) {
  i64 oracle{};
  const bool add_overflow = __builtin_add_overflow(a, b, &oracle);
  if (add_overflow) require(!checked_add(a, b));
  else require(checked_add(a, b) == std::optional<i64>{oracle});
  const bool sub_overflow = __builtin_sub_overflow(a, b, &oracle);
  if (sub_overflow) require(!checked_sub(a, b));
  else require(checked_sub(a, b) == std::optional<i64>{oracle});
  const bool mul_overflow = __builtin_mul_overflow(a, b, &oracle);
  if (mul_overflow) require(!checked_mul(a, b));
  else require(checked_mul(a, b) == std::optional<i64>{oracle});
  const bool division_undefined =
      b == 0 || (a == std::numeric_limits<i64>::min() && b == -1);
  if (division_undefined) {
    require(!checked_div(a, b));
    require(!checked_rem(a, b));
  } else {
    require(checked_div(a, b) == std::optional<i64>{a / b});
    require(checked_rem(a, b) == std::optional<i64>{a % b});
  }
}

int main() {
  constexpr i64 min = std::numeric_limits<i64>::min();
  constexpr i64 max = std::numeric_limits<i64>::max();
  constexpr std::array<i64, 9> boundaries{min, min + 1, -2, -1, 0, 1, 2, max - 1, max};
  for (const auto a : boundaries) {
    for (const auto b : boundaries) {
      check(static_cast<i64>(a), static_cast<i64>(b));
    }
  }
  Splitmix64 random;
  for (unsigned i = 0; i < 100000; ++i) {
    const i64 a = std::bit_cast<i64>(random.next());
    const i64 b = std::bit_cast<i64>(random.next());
    check(a, b);
    // Test near-zero divisors and opposite boundaries independently.
    check(a, static_cast<i64>(static_cast<int>(i % 17) - 8));
    check(min, b);
    check(max, b);
  }
}
