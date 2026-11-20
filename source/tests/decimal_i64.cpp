#include "atto/decimal_i64.hpp"
#include <cstdlib>
#include <limits>
#include <optional>
#include <string>
#include <string_view>

using atto::forge::i64;
using atto::forge::parse_decimal_i64;
constexpr i64 MIN = std::numeric_limits<i64>::min();
constexpr i64 MAX = std::numeric_limits<i64>::max();
static_assert(parse_decimal_i64("0") == std::optional<i64>{0});
static_assert(parse_decimal_i64("+00042") == std::optional<i64>{42});
static_assert(parse_decimal_i64("-0") == std::optional<i64>{0});
static_assert(parse_decimal_i64("9223372036854775807") == std::optional<i64>{MAX});
static_assert(parse_decimal_i64("-9223372036854775808") == std::optional<i64>{MIN});
static_assert(!parse_decimal_i64("9223372036854775808"));
static_assert(!parse_decimal_i64("-9223372036854775809"));
static_assert(!parse_decimal_i64(""));

static void require(bool ok) {
  if (!ok) std::abort();
}
int main() {
  for (i64 n = -10000; n <= 10000; ++n) {
    const auto token = std::to_string(n);
    require(parse_decimal_i64(token) == std::optional<i64>{n});
    require(parse_decimal_i64("+" + token) == (n < 0 ? std::nullopt : std::optional<i64>{n}));
  }
  for (auto token : {"", "+", "-", " 42", "42 ", "--1", "++1", "+-1",
                     "0x10", "1_000", "1.0", "12; exit", "\\n1",
                     "99999999999999999999999999999999999",
                     "-9223372036854775809", "9223372036854775808"}) {
    require(!parse_decimal_i64(token));
  }
  const std::string embedded_nul("4\0" "2", 3);
  require(!parse_decimal_i64(std::string_view(embedded_nul)));
  require(!parse_decimal_i64(std::string(33, '0')));
  require(parse_decimal_i64("-9223372036854775808") == std::optional<i64>{MIN});
  require(parse_decimal_i64("9223372036854775807") == std::optional<i64>{MAX});
}
