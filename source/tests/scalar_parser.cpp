#include "atto/scalar_parser.hpp"
#include <cassert>
#include <limits>
#include <string>
using namespace atto::forge;
using namespace atto::forge::scalar;
int main() {
  // Pointer<int> ref!/deref!/unref! is admitted only by the distinct
  // closed-world local-pointer parser, never as an untyped ownership call.
  for (const std::string_view expression : {
      "copy!42", "take!value", "borrow! mut owner",
      "share!owner", "pin!owner", "drop!owner",
      "freeze!owner", "weak!owner", "copy!eq\xC3\xA9" }) {
    const auto source = std::string("fnc main(): int {return ") +
                        std::string(expression) + ";}";
    const auto rejected = parse_source(source);
    assert(rejected.error == ParseError::ownership_bang_unsupported);
    assert(parse_error_name(rejected.error) == "E_OWNERSHIP_BANG_UNSUPPORTED");
  }
  const auto ordinary_comparison =
      parse_source("fnc main(): int { return copy!eq other; }");
  assert(ordinary_comparison.error != ParseError::ownership_bang_unsupported);
  const auto nested_comment = parse_source(
      "fnc main(): int {return /* outer /* copy!secret */ remaining */ 42;}");
  assert(nested_comment.ok());
  assert(nested_comment.nodes[nested_comment.root].value == 42);
  auto commented = parse_source(
      "/* module header */ fnc main(): int { // body\n"
      " return 8 / /* denominator */ 2; /* tail */ }");
  assert(commented.ok());
  auto malformed_comment = parse_source("fnc main(): int {return 1; /*");
  assert(!malformed_comment.ok() && malformed_comment.error == ParseError::lexical);
  for (const auto* signature : {
    "fnc main() -> int {return 42;}",
    "fnc main() : int -> {return 42;}",
    "fnc int <- main() {return 42;}",
    "int <- main() {return 42;}",
    "pub fnc int <- main() {return 42;}",
    "pub fnc main() -> int {return 42;}"
  }) assert(parse_source(signature).ok());
  for (const auto* inferred : {
    "fnc main(): infer {return 40+2;}",
    "fnc main() -> infer {return 6*7;}",
    "fnc main(): infer -> {return 42;}",
    "fnc infer <- main() {return 42;}",
    "infer <- main() {return 42;}",
    "pub fnc main() -> infer {return -1 >>> 1;}"
  }) assert(parse_source(inferred).ok());
  for (const auto* bad_inferred : {
    "fnc main(): infer {return;}",
    "fnc main() -> infer {return true;}",
    "fnc main(): infer {return \"hello\";}",
    "fnc main() -> infer -> {return 42;}",
    "fnc main() -> bool {return 42;}"
  }) assert(!parse_source(bad_inferred).ok());
  for (const auto* bad_signature : {
    "fnc main() -> int -> {return 42;}",
    "fnc main() -> {return 42;}",
    "fnc main() : -> int {return 42;}",
    "fnc int <- other() {return 42;}",
    "fnc main() <- int {return 42;}",
    "fnc main() -> string {return 42;}",
    "int -> main() {return 42;}"
  }) assert(!parse_source(bad_signature).ok());
  auto a = parse_source("pub fnc main(): int { return 4 + 5 * (6 - 1); }");
  assert(a.ok() && a.nodes[a.root].op == Op::add);
  assert(a.nodes.size() == 7);
  auto ops = parse_source("fnc main(): int {return ~0 & 15 | 1 ^ 2;}");
  assert(ops.ok() && ops.nodes[ops.root].op == Op::bor);
  auto b = parse_source("fnc main(): int {return -9223372036854775808;}");
  assert(b.ok() && b.nodes[b.root].value == std::numeric_limits<i64>::min());
  auto padded_min = parse_source("fnc main(): int {return -0009223372036854775808;}");
  assert(padded_min.ok() &&
         padded_min.nodes[padded_min.root].value == std::numeric_limits<i64>::min());
  auto padded_over = parse_source("fnc main(): int {return -0009223372036854775809;}");
  assert(!padded_over.ok() &&
         padded_over.error == ParseError::literal_out_of_range);
  auto c = parse_source("fnc main(): int {return -(-9223372036854775808);}");
  assert(c.ok() && c.nodes[c.root].op == Op::neg);
  for (const auto* bad : {
    "", "fnc nope(): int {return 1;}", "fnc main(int x): int {return 1;}",
    "fnc main(): int {return 1; return 2;}",
    "fnc main(): int {return 1}","fnc main(): int {return 1; } junk",
    "fnc main(): int {return 1 + ;}",
    "fnc main(): int {return 9223372036854775808;}",
    "fnc main(): int {return -9223372036854775809;}",
    "fnc main(): int {return x;}",
    "fnc main(): int {return 42 == 42;}",
    "fnc main(): int {return 2 ** 3;}",
    "fnc main(): int {return (1;}",
    "fnc main(): int {return 1;}\nfnc evil():int{return 2;}",
    "fnc main(): int {return @;}"
  }) assert(!parse_source(bad).ok());
  auto overflow = parse_source("fnc main(): int {return 9223372036854775808;}");
  assert(overflow.error == ParseError::literal_out_of_range);
  std::string deep(40, '(');
  deep += "1";
  deep += std::string(40, ')');
  auto nested = parse_source("fnc main(): int { return " + deep + "; }");
  assert(nested.error == ParseError::depth_limit);
  const std::string within(max_parse_depth, '(');
  const std::string close(max_parse_depth, ')');
  assert(parse_source("fnc main(): int {return " + within + "42" + close + ";}").ok());
  assert(parse_source("fnc main(): int {return " + within + "(42)" + close + ";}").error == ParseError::depth_limit);
  const std::string unary_within(max_parse_depth, '~');
  assert(parse_source("fnc main(): int {return " + unary_within + "42;}").ok());
  assert(parse_source("fnc main(): int {return " + unary_within + "~42;}").error == ParseError::depth_limit);
  std::string flat = "fnc main(): int {return 1";
  for (int i = 0; i < 60; ++i) flat += "+1";
  flat += ";}";
  assert(parse_source(flat).ok());
  assert(nested.error == ParseError::depth_limit);
  std::string many = "fnc main(): int {return 1";
  for (int i = 0; i < 70; ++i) many += "+1";
  many += ";}";
  auto budget = parse_source(many);
  assert(budget.error == ParseError::node_limit);
  assert(parse_source(std::string(16385, 'a')).error == ParseError::lexical);
}
