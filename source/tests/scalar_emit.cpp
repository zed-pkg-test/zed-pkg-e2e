#include "atto/scalar_emit.hpp"
#include <cassert>
#include <string>
using namespace atto::forge::scalar;
int main() {
  const auto source = "pub fnc main(): int {return 4 + 5 * 6;}";
  const auto c = emit_cpp_source(source);
  assert(c && c == emit_cpp_source(source));
  assert(c->find("checked_add") != std::string::npos);
  assert(c->find("checked_mul") != std::string::npos);
  assert(c->find("std::printf") != std::string::npos);
  const auto mn = emit_cpp_source(
    "fnc main(): int {return -9223372036854775808;}");
  assert(mn && mn->find("numeric_limits<atto::forge::i64>::min()") != std::string::npos);
  const auto bitwise = emit_cpp_source(
    "fnc main(): int {return ~0 & 15 | 1 ^ 2;}");
  assert(bitwise && bitwise->find("bit_not") != std::string::npos);
  assert(bitwise->find("bit_and") != std::string::npos);
  assert(bitwise->find("bit_or") != std::string::npos);
  assert(bitwise->find("bit_xor") != std::string::npos);
  const auto traps = emit_cpp_source("fnc main(): int {return 3 / 0;}");
  assert(traps && traps->find("E_DIVZERO") != std::string::npos);
  assert(traps->find("return 71") != std::string::npos);
  const auto overflow = emit_cpp_source(
    "fnc main(): int {return 9223372036854775807 + 1;}");
  assert(overflow && overflow->find("E_OVERFLOW") != std::string::npos);
  assert(!emit_cpp_source("fnc main(): int {return 2;}; int malicious(){}"));
  assert(!emit_cpp_source("fnc main(): int {return system; }"));
  auto malformed = parse_source(source);
  assert(malformed.ok());
  malformed.nodes[0].op = Op::add;
  malformed.nodes[0].left = 0;
  assert(!emit_cpp(malformed));
  auto detached = parse_source(source);
  assert(detached.ok() && validate_ast(detached));
  detached.root = 0;
  assert(!validate_ast(detached));
  assert(!emit_cpp(detached));
  auto stray = parse_source(source);
  stray.nodes.push_back(Node{Op::literal, 0, 0, 100});
  stray.root = stray.nodes.size() - 1;
  assert(!validate_ast(stray));
  assert(!emit_cpp(stray));
  auto forged = parse_source(source);
  forged.nodes.back().op = static_cast<Op>(900);
  assert(!validate_ast(forged));
  assert(!emit_cpp(forged));
}
