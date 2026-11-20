#include "atto/scalar_emit.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <system_error>

namespace fs = std::filesystem;
using atto::forge::scalar::max_source_bytes;
using atto::forge::scalar::emit_cpp_source;
static int deny(const char* msg) { std::cerr << msg << '\n'; return 2; }

int main(int argc, char** argv) {
  // Side-effect-free bounded source admission for editors and CI.
  if (argc == 3 && std::string(argv[1]) == "--check") {
    const fs::path input(argv[2]);
    if (input.extension() != ".atto")
      return deny("E_PATH: expected .atto input");
    std::error_code check_error;
    if (!fs::is_regular_file(input, check_error) || check_error)
      return deny("E_SOURCE: not a regular input file");
    const auto size = fs::file_size(input, check_error);
    if (check_error || size > max_source_bytes)
      return deny("E_SOURCE_LIMIT");
    std::ifstream file(input, std::ios::binary);
    if (!file) return deny("E_SOURCE: read failed");
    std::string text(static_cast<std::size_t>(size), '\0');
    if (!text.empty()) file.read(text.data(), static_cast<std::streamsize>(size));
    if (!file || file.peek() != std::char_traits<char>::eof())
      return deny("E_SOURCE: changing or truncated input");
    const auto parsed = atto::forge::scalar::parse_source(text);
    if (!parsed.ok()) {
      std::cerr << atto::forge::scalar::parse_error_name(parsed.error)
                << " byte=" << parsed.error_offset << '\n';
      return 2;
    }
    if (!atto::forge::scalar::validate_ast(parsed))
      return deny("E_AST: scalar AST rejected");
    return 0;
  }
  if (argc != 4 || std::string(argv[2]) != "-o")
    return deny("E_USAGE: forge_scalar input.atto -o output.cpp");
  const fs::path input(argv[1]), output(argv[3]);
  if (input.extension() != ".atto" || output.extension() != ".cpp")
    return deny("E_PATH: expected .atto and .cpp");
  std::error_code ec;
  if (!fs::is_regular_file(input, ec) || ec)
    return deny("E_SOURCE: not a regular input file");
  ec.clear();
  const auto in_absolute = fs::absolute(input, ec);
  if (ec) return deny("E_PATH: invalid input");
  ec.clear();
  const auto out_absolute = fs::absolute(output, ec);
  if (ec || in_absolute.lexically_normal() == out_absolute.lexically_normal())
    return deny("E_PATH: source/output alias");
  // Disallow symlinked ancestor directories as well as symlink basenames.
  // This is a fail-closed preflight, not a replacement for OS-level sandboxing
  // or atomic directory-descriptor-relative file operations.
  fs::path candidate_parent = out_absolute.lexically_normal().root_path();
  for (const auto& part : out_absolute.lexically_normal().relative_path().parent_path()) {
    candidate_parent /= part;
    ec.clear();
    const auto status = fs::symlink_status(candidate_parent, ec);
    if (ec || fs::is_symlink(status) || !fs::is_directory(status))
      return deny("E_PATH: unsafe output parent");
  }
  ec.clear();
  if (fs::exists(output, ec)) {
    if (ec) return deny("E_PATH: invalid output");
    ec.clear();
    if (fs::equivalent(input, output, ec) && !ec)
      return deny("E_PATH: hard-link alias");
  }
  // is_symlink(path, ec) may set ENOENT for a new destination.
  // Only classify an existing path as a symlink; new outputs are valid.
  // exists(path) follows links: dangling links report false and must still
  // be rejected. symlink_status examines the directory entry itself.
  auto unsafe_target = [](const fs::path& path, bool temporary) {
    std::error_code err;
    const auto status = fs::symlink_status(path, err);
    if (err == std::errc::no_such_file_or_directory) return false;
    if (err) return true;
    if (fs::is_symlink(status)) return true;
    if (!fs::exists(status)) return false;
    // Never truncate or overwrite a pre-existing temporary artifact.
    if (temporary) return true;
    return !fs::is_regular_file(status);
  };
  if (unsafe_target(output, false))
    return deny("E_PATH: output symlink or unsafe file");
  const fs::path temporary = output.string() + ".part";
  if (unsafe_target(temporary, true))
    return deny("E_PATH: temporary path already exists or is unsafe");
  // Never delete user-selected existing output on any rejected source.
  ec.clear();
  const auto size = fs::file_size(input, ec);
  if (ec || size > max_source_bytes) {
    return deny("E_SOURCE_LIMIT");
  }
  std::ifstream file(input, std::ios::binary);
  if (!file) return deny("E_SOURCE: read failed");
  std::string source(static_cast<std::size_t>(size), '\0');
  if (!source.empty()) file.read(source.data(), static_cast<std::streamsize>(size));
  if (!file || file.peek() != std::char_traits<char>::eof()) {
    return deny("E_SOURCE: changing or truncated input");
  }
  const auto parsed = atto::forge::scalar::parse_source(source);
  if (!parsed.ok()) {
    std::cerr << atto::forge::scalar::parse_error_name(parsed.error)
              << " byte=" << parsed.error_offset << '\n';
    return 2;
  }
  const auto generated = atto::forge::scalar::emit_cpp(parsed);
  if (!generated) {
    return deny("E_AST: scalar AST rejected before code emission");
  }
  std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
  if (!out) return deny("E_OUTPUT: open failed");
  out.write(generated->data(), static_cast<std::streamsize>(generated->size()));
  out.flush();
  const bool written = static_cast<bool>(out);
  out.close();
  if (!written || !out) {
    ec.clear(); fs::remove(temporary, ec);
    return deny("E_OUTPUT: incomplete write");
  }
  ec.clear();
  fs::rename(temporary, output, ec);
  if (ec) {
    ec.clear(); fs::remove(temporary, ec);
    return deny("E_OUTPUT: publication failed");
  }
  return 0;
}
