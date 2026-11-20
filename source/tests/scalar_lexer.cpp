#include "atto/scalar_lexer.hpp"
#include <cassert>
#include <string>
#include <string_view>
using namespace atto::forge::scalar;

int main() {
  const std::string source = "pub fnc main(): int { return -9223372036854775808 + 3 * (8/2)%7; }";
  auto l = tokenize(source);
  assert(l.ok());
  assert(l.tokens.size() > 20);
  assert(l.tokens[0].kind == Kind::name);
  assert(source.substr(l.tokens[0].offset, l.tokens[0].length) == "pub");
  assert(l.tokens[1].kind == Kind::name);
  assert(l.tokens[2].kind == Kind::name);
  assert(l.tokens.back().kind == Kind::end);
  for (const auto& t : l.tokens) assert(t.offset + t.length <= source.size());
  assert(tokenize("\r\n fnc main () : int { return 0; }\t").ok());
  assert(tokenize("").ok());
  auto signatures = tokenize("fnc main() -> int {} int <- main() {}");
  assert(signatures.ok());
  bool saw_arrow = false, saw_left_arrow = false;
  for (const auto& token : signatures.tokens) {
    saw_arrow = saw_arrow || token.kind == Kind::arrow;
    saw_left_arrow = saw_left_arrow || token.kind == Kind::left_arrow;
  }
  assert(saw_arrow && saw_left_arrow);
  auto comments = tokenize("/* prefix */ 3 // line\n + /* block\nline */ 4 / 2");
  assert(comments.ok());
  assert(comments.tokens.size() == 6);
  assert(comments.tokens[0].kind == Kind::integer);
  assert(comments.tokens[1].kind == Kind::plus);
  assert(comments.tokens[3].kind == Kind::slash);
  const auto unterminated = tokenize("1 + /* not closed");
  assert(unterminated.error == LexError::unterminated_comment);
  assert(unterminated.error_offset == 4);
  assert(tokenize("// trailing comment without newline").ok());
  assert(tokenize("//" + std::string(max_source_bytes - 2, '/')).ok());
  assert(tokenize("/* */").tokens.size() == 1);
  assert(!tokenize("fnc main(){return @;}").ok());
  const std::string nul("1\0" "2", 3);
  auto bad = tokenize(std::string_view(nul));
  assert(bad.error == LexError::unsupported_byte && bad.error_offset == 1);
  const auto bits = tokenize("~1 & 2 | 3 ^ 4");
  assert(bits.ok());
  assert(bits.tokens[0].kind == Kind::tilde);
  assert(bits.tokens[2].kind == Kind::bit_and);
  assert(bits.tokens[4].kind == Kind::bit_or);
  assert(bits.tokens[6].kind == Kind::bit_xor);
  assert(tokenize("\xC3\xA9").error == LexError::unsupported_byte);
  assert(tokenize(std::string(max_source_bytes + 1, 'a')).error == LexError::source_too_large);
  assert(tokenize(std::string(max_tokens + 1, '+')).error == LexError::token_limit);
  assert(tokenize(std::string(max_tokens, '+')).ok());
  // Pointer-local tokens share the same lexer as scalar shifts and arrows.
  const std::string lease = "Pointer<int> ptr = ref!value; deref!ptr; unref!ptr;";
  const auto pointer_tokens = tokenize(lease);
  assert(pointer_tokens.ok());
  bool less = false, greater = false, assign = false, bang = false;
  for (std::size_t j = 0; j < pointer_tokens.tokens.size(); ++j) {
    const auto& tok = pointer_tokens.tokens[j];
    less = less || tok.kind == Kind::lt;
    greater = greater || tok.kind == Kind::gt;
    assign = assign || tok.kind == Kind::assign;
    if (tok.kind == Kind::bang) {
      assert(j > 0);
      const auto& before = pointer_tokens.tokens[j - 1];
      assert(before.kind == Kind::name);
      assert(before.offset + before.length == tok.offset);
      bang = true;
    }
  }
  assert(less && greater && assign && bang);
  const auto distinct_shifts = tokenize("1 <<< 2 >>> 1");
  assert(distinct_shifts.ok());
  assert(distinct_shifts.tokens[1].kind == Kind::shl);
  assert(distinct_shifts.tokens[3].kind == Kind::lshr);
  const auto distinct_arrow = tokenize("int <- main()");
  assert(distinct_arrow.tokens[1].kind == Kind::left_arrow);

}
