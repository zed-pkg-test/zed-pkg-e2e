#include "atto/checked_i64.hpp"

#include <cstdint>
#include <cstdlib>
#include <limits>
#include <optional>

using atto::forge::shl_i64;
using atto::forge::lshr_i64;
using atto::forge::ashr_i64;
using atto::forge::bit_and;
using atto::forge::bit_or;
using atto::forge::bit_xor;
using atto::forge::bit_not;
using atto::forge::checked_add;
using atto::forge::checked_sub;
using atto::forge::checked_mul;
using atto::forge::checked_div;
using atto::forge::checked_rem;
using atto::forge::checked_neg;
using atto::forge::checked_abs;
using atto::forge::i64;
constexpr i64 MIN = std::numeric_limits<i64>::min();
constexpr i64 MAX = std::numeric_limits<i64>::max();

static_assert(checked_add(1, 2) == std::optional<i64>{3});
static_assert(!checked_add(MAX, 1));
static_assert(checked_sub(MIN, 0) == std::optional<i64>{MIN});
static_assert(!checked_sub(MIN, 1));
static_assert(checked_mul(MIN, 1) == std::optional<i64>{MIN});
static_assert(!checked_mul(MIN, -1));
static_assert(checked_mul(-3, -7) == std::optional<i64>{21});

static_assert(checked_div(-7, 3) == std::optional<i64>{-2});
static_assert(checked_rem(-7, 3) == std::optional<i64>{-1});
static_assert(!checked_div(1, 0));
static_assert(!checked_rem(1, 0));
static_assert(!checked_div(MIN, -1));
static_assert(!checked_rem(MIN, -1));

static_assert(!checked_neg(MIN));
static_assert(!checked_abs(MIN));
static_assert(checked_neg(MAX) == std::optional<i64>{-MAX});
static_assert(checked_abs(-7) == std::optional<i64>{7});
static_assert(checked_abs(0) == std::optional<i64>{0});

static_assert(bit_not(0) == std::optional<i64>{-1});
static_assert(bit_and(-1, 42) == std::optional<i64>{42});
static_assert(bit_or(1, 4) == std::optional<i64>{5});
static_assert(bit_xor(6, 3) == std::optional<i64>{5});
static_assert(bit_not(MIN) == std::optional<i64>{MAX});

static_assert(shl_i64(1, 63) == std::optional<i64>{MIN});
static_assert(shl_i64(1, 64) == std::optional<i64>{1});
static_assert(shl_i64(1, -1) == std::optional<i64>{MIN});
static_assert(lshr_i64(-1, 1) == std::optional<i64>{MAX});
static_assert(lshr_i64(MIN, 63) == std::optional<i64>{1});
static_assert(ashr_i64(-8, 2) == std::optional<i64>{-2});
static_assert(ashr_i64(-1, 63) == std::optional<i64>{-1});
static_assert(ashr_i64(MIN, 63) == std::optional<i64>{-1});
static_assert(ashr_i64(MIN, 64) == std::optional<i64>{MIN});

void require(bool condition) {
  if (!condition) std::abort();
}

int main() {
  require(!checked_add(MAX, 1));
  require(!checked_add(MIN, -1));
  require(checked_add(MIN, MAX) == std::optional<i64>{-1});
  require(!checked_sub(MAX, -1));
  require(!checked_sub(MIN, 1));
  require(checked_sub(MIN, MIN) == std::optional<i64>{0});
  require(checked_sub(MAX, MIN) == std::nullopt);
  require(checked_mul(0, MIN) == std::optional<i64>{0});
  require(checked_mul(MIN, 1) == std::optional<i64>{MIN});
  require(!checked_mul(MIN, -1));
  require(!checked_mul(MAX, 2));
  require(!checked_mul(MIN, 2));
  require(checked_mul(MAX, -1) == std::optional<i64>{-MAX});
  require(checked_mul(-1, -1) == std::optional<i64>{1});
  require(checked_mul(MIN / 2, 2) == std::optional<i64>{MIN});
  require(checked_div(MIN, 1) == std::optional<i64>{MIN});
  require(checked_rem(MIN, 1) == std::optional<i64>{0});
  require(checked_div(MAX, -1) == std::optional<i64>{-MAX});
  require(checked_rem(MAX, -1) == std::optional<i64>{0});
  require(!checked_div(MIN, -1));
  require(!checked_rem(MIN, -1));
  require(!checked_div(0, 0));
  require(!checked_rem(0, 0));
  require(!checked_neg(MIN));
  require(!checked_abs(MIN));
  require(checked_neg(MAX) == std::optional<i64>{-MAX});
  require(checked_abs(-MAX) == std::optional<i64>{MAX});
  // Exhaustive small-domain arithmetic: oracle operations cannot overflow.
  for (i64 a = -128; a <= 128; ++a) {
    require(checked_neg(a) == std::optional<i64>{-a});
    require(checked_abs(a) == std::optional<i64>{a < 0 ? -a : a});
    for (i64 b = -128; b <= 128; ++b) {
      require(checked_add(a, b) == std::optional<i64>{a + b});
      require(checked_sub(a, b) == std::optional<i64>{a - b});
      require(checked_mul(a, b) == std::optional<i64>{a * b});
      if (b == 0) {
        require(!checked_div(a, b));
        require(!checked_rem(a, b));
      } else {
        require(checked_div(a, b) == std::optional<i64>{a / b});
        require(checked_rem(a, b) == std::optional<i64>{a % b});
      }
    }
  }
}
