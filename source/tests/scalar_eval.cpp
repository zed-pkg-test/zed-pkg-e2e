#include "atto/scalar_eval.hpp"
#include <cassert>
#include <limits>
#include <string>
using namespace atto::forge;
using namespace atto::forge::scalar;
static std::string program(const std::string& expr) {
  return "pub fnc main(): int { return " + expr + "; }";
}
int main() {
  struct Case { const char* expr; i64 expected; };
  for (const auto c : {
    Case{"42",42}, {"4 + 5 * 6",34}, {"(4 + 5) * 6",54},
    {"10 - 2 * 3",4}, {"-10 + +3",-7}, {"-7 / 3",-2},
    {"-7 % 3",-1}, {"7 % -3",1}, {"-7 % -3",-1},
    {"-9223372036854775808",std::numeric_limits<i64>::min()},
    {"-0009223372036854775808",std::numeric_limits<i64>::min()},
    {"9223372036854775807",std::numeric_limits<i64>::max()},
    {"-9223372036854775808 + 1",std::numeric_limits<i64>::min() + 1},
    {"8/2*3+1",13},
    {"1 <<< 63",std::numeric_limits<i64>::min()},
    {"1 <<< 64",1}, {"1 <<< -1",std::numeric_limits<i64>::min()},
    {"-1 >>> 1",std::numeric_limits<i64>::max()},
    {"-8 ashr 2",-2}, {"(1 <<< 63) >>> 63",1},
    {"-8 ashr 2 + 1",-1}, {"~0",-1}, {"-1 & 42",42}, {"1 | 4",5}, {"6 ^ 3",5},
    {"~0 & 255",255}, {"1 | 2 & 4",1}, {"1 ^ 3 & 2",3},
    {"1 | 2 ^ 3 & 2",1}, {"~(-9223372036854775808)",9223372036854775807LL}
  }) {
    const auto value = evaluate_source(program(c.expr));
    assert(value.ok() && *value.value == c.expected);
  }
  for (const char* expr : {
    "9223372036854775807 + 1",
    "-9223372036854775808 - 1",
    "-0009223372036854775808 - 1",
    "-9223372036854775808 / -1",
    "-9223372036854775808 % -1",
    "-(-9223372036854775808)",
    "9223372036854775807 * 2"
  }) assert(evaluate_source(program(expr)).error == EvalError::overflow);
  for (const char* expr : {"1 / 0", "2 % 0", "1 / (7 - 7)"})
    assert(evaluate_source(program(expr)).error == EvalError::divide_by_zero);
  assert(evaluate_source(program("abc")).error == EvalError::parse_error);
  auto ast = parse_source(program("5+3"));
  assert(ast.ok());
  ast.nodes[0].op = Op::add; ast.nodes[0].left = 0;
  assert(evaluate(ast).error == EvalError::invalid_ast);
  auto detached = parse_source(program("5+3"));
  assert(detached.ok() && validate_ast(detached));
  detached.root = 0;
  assert(!validate_ast(detached));
  assert(evaluate(detached).error == EvalError::invalid_ast);
  auto stray = parse_source(program("5+3"));
  stray.nodes.push_back(Node{Op::literal, 0, 0, 12});
  stray.root = stray.nodes.size()-1;
  assert(!validate_ast(stray));
  assert(evaluate(stray).error == EvalError::invalid_ast);
  auto forged = parse_source(program("5+3"));
  forged.nodes.back().op = static_cast<Op>(999);
  assert(!validate_ast(forged));
  assert(evaluate(forged).error == EvalError::invalid_ast);
  auto bad_unary = parse_source(program("~7"));
  assert(bad_unary.ok());
  bad_unary.nodes.back().right = 1;
  assert(!validate_ast(bad_unary));
  assert(evaluate(bad_unary).error == EvalError::invalid_ast);
}
