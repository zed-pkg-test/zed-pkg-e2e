cmake_minimum_required(VERSION 3.20)
project(atto_forge_checked_i64 LANGUAGES CXX)

include(CTest)
option(ATTO_FORGE_SANITIZERS "Use ASan and UBSan for native tests" ON)

add_library(atto_forge_checked_i64 INTERFACE)
target_include_directories(atto_forge_checked_i64 INTERFACE
  ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(atto_forge_checked_i64 INTERFACE cxx_std_20)

add_executable(checked_i64_test tests/checked_i64.cpp)
target_link_libraries(checked_i64_test PRIVATE atto_forge_checked_i64)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(checked_i64_test PRIVATE -Wall -Wextra -Werror -pedantic)
  if(ATTO_FORGE_SANITIZERS)
    target_compile_options(checked_i64_test PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(checked_i64_test PRIVATE -fsanitize=address,undefined)
  endif()
endif()
add_test(NAME checked_i64_test COMMAND checked_i64_test)

# Host-only ownership contract model: not a Forge guest compiler.
# Run sanitizer-backed native lifetime and concurrent weak-reference tests.
find_package(Threads REQUIRED)
add_executable(ownership_v1_test tests/ownership_v1.cpp)
target_link_libraries(ownership_v1_test PRIVATE atto_forge_checked_i64 Threads::Threads)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  # Ensure assertions remain enabled even under -DCMAKE_BUILD_TYPE=Release.
  target_compile_options(ownership_v1_test PRIVATE -Wall -Wextra -Werror -pedantic -UNDEBUG)
  if(ATTO_FORGE_SANITIZERS)
    target_compile_options(ownership_v1_test PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(ownership_v1_test PRIVATE -fsanitize=address,undefined)
  endif()
endif()
if(MSVC)
  target_compile_options(ownership_v1_test PRIVATE /UNDEBUG)
endif()
add_test(NAME ownership_v1_test COMMAND ownership_v1_test)
# Deterministic native stress matrix: malformed graphs, exact budgets,
# concurrent region retirement and immovable in-place pin construction.
add_executable(ownership_stress_v1_test tests/ownership_stress_v1.cpp)
target_link_libraries(ownership_stress_v1_test PRIVATE atto_forge_checked_i64 Threads::Threads)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(ownership_stress_v1_test PRIVATE -Wall -Wextra -Werror -pedantic -UNDEBUG)
  if(ATTO_FORGE_SANITIZERS)
    target_compile_options(ownership_stress_v1_test PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(ownership_stress_v1_test PRIVATE -fsanitize=address,undefined)
  endif()
elseif(MSVC)
  target_compile_options(ownership_stress_v1_test PRIVATE /UNDEBUG)
endif()
add_test(NAME ownership_stress_v1_test COMMAND ownership_stress_v1_test)


# Native bounded literal reader; host-only scalar primitive.
add_executable(decimal_i64_test tests/decimal_i64.cpp)
target_link_libraries(decimal_i64_test PRIVATE atto_forge_checked_i64)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(decimal_i64_test PRIVATE -Wall -Wextra -Werror -pedantic)
  if(ATTO_FORGE_SANITIZERS)
    target_compile_options(decimal_i64_test PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(decimal_i64_test PRIVATE -fsanitize=address,undefined)
  endif()
endif()
add_test(NAME decimal_i64_test COMMAND decimal_i64_test)

# Restricted native lexer has no Python or borrowed source buffers.
add_executable(scalar_lexer_test tests/scalar_lexer.cpp)
target_link_libraries(scalar_lexer_test PRIVATE atto_forge_checked_i64)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(scalar_lexer_test PRIVATE -Wall -Wextra -Werror -pedantic -UNDEBUG)
  if(ATTO_FORGE_SANITIZERS)
    target_compile_options(scalar_lexer_test PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(scalar_lexer_test PRIVATE -fsanitize=address,undefined)
  endif()
endif()
add_test(NAME scalar_lexer_test COMMAND scalar_lexer_test)

# Strict, bounded native scalar AST parser; no guest execution claim.
add_executable(scalar_parser_test tests/scalar_parser.cpp)
target_link_libraries(scalar_parser_test PRIVATE atto_forge_checked_i64)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(scalar_parser_test PRIVATE -Wall -Wextra -Werror -pedantic -UNDEBUG)
  if(ATTO_FORGE_SANITIZERS)
    target_compile_options(scalar_parser_test PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(scalar_parser_test PRIVATE -fsanitize=address,undefined)
  endif()
endif()
add_test(NAME scalar_parser_test COMMAND scalar_parser_test)

# Checked native scalar AST evaluator: no guest execution claim.
add_executable(scalar_eval_test tests/scalar_eval.cpp)
target_link_libraries(scalar_eval_test PRIVATE atto_forge_checked_i64)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(scalar_eval_test PRIVATE -Wall -Wextra -Werror -pedantic -UNDEBUG)
  if(ATTO_FORGE_SANITIZERS)
    target_compile_options(scalar_eval_test PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(scalar_eval_test PRIVATE -fsanitize=address,undefined)
  endif()
endif()
add_test(NAME scalar_eval_test COMMAND scalar_eval_test)

# Deterministic C++ emission from the bounded scalar AST.
add_executable(scalar_emit_test tests/scalar_emit.cpp)
target_link_libraries(scalar_emit_test PRIVATE atto_forge_checked_i64)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(scalar_emit_test PRIVATE -Wall -Wextra -Werror -pedantic -UNDEBUG)
  if(ATTO_FORGE_SANITIZERS)
    target_compile_options(scalar_emit_test PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(scalar_emit_test PRIVATE -fsanitize=address,undefined)
  endif()
endif()
add_test(NAME scalar_emit_test COMMAND scalar_emit_test)

# Real compiled restricted scalar guests; no Python bootstrap.
add_executable(forge_scalar tools/forge_scalar.cpp)
target_link_libraries(forge_scalar PRIVATE atto_forge_checked_i64)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(forge_scalar PRIVATE -Wall -Wextra -Werror -pedantic)
  if(ATTO_FORGE_SANITIZERS)
    target_compile_options(forge_scalar PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(forge_scalar PRIVATE -fsanitize=address,undefined)
  endif()
endif()
add_test(NAME scalar_guest_pipeline
  COMMAND "${CMAKE_COMMAND}"
    "-DROOT=${CMAKE_CURRENT_SOURCE_DIR}"
    "-DCXX=${CMAKE_CXX_COMPILER}"
    "-DSANITIZERS=${ATTO_FORGE_SANITIZERS}"
    "-DFORGE=$<TARGET_FILE:forge_scalar>"
    "-DOUT_DIR=${CMAKE_CURRENT_BINARY_DIR}/scalar-guests"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/scalar_guest.cmake")

# Executed generated-native-guest conformance corpus under GCC and Clang.
add_test(NAME scalar_guest_conformance
  COMMAND "${CMAKE_COMMAND}"
    "-DROOT=${CMAKE_CURRENT_SOURCE_DIR}"
    "-DCXX=${CMAKE_CXX_COMPILER}"
    "-DSANITIZERS=${ATTO_FORGE_SANITIZERS}"
    "-DFORGE=$<TARGET_FILE:forge_scalar>"
    "-DOUT_DIR=${CMAKE_CURRENT_BINARY_DIR}/scalar-conformance"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/scalar_conformance.cmake")

# Failed compiler inputs yield typed bounded diagnostics, no echoed source.
add_test(NAME scalar_cli_diagnostics
  COMMAND "${CMAKE_COMMAND}"
    "-DFORGE=$<TARGET_FILE:forge_scalar>"
    "-DOUT_DIR=${CMAKE_CURRENT_BINARY_DIR}/scalar-diagnostics"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/scalar_cli_diagnostics.cmake")

# Compiler builtins independently attest checked-i64 primitives at boundary
# and 100,000 deterministic high-entropy signed operand pairs.
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  add_executable(checked_i64_oracle_test tests/checked_i64_oracle.cpp)
  target_link_libraries(checked_i64_oracle_test PRIVATE atto_forge_checked_i64)
  target_compile_options(checked_i64_oracle_test PRIVATE -Wall -Wextra -Werror -pedantic)
  if(ATTO_FORGE_SANITIZERS)
    target_compile_options(checked_i64_oracle_test PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(checked_i64_oracle_test PRIVATE -fsanitize=address,undefined)
  endif()
  add_test(NAME checked_i64_oracle_test COMMAND checked_i64_oracle_test)
endif()

# Deterministic native emission verified with 48 compiled executable guests.
add_test(NAME scalar_guest_reproducibility
  COMMAND "${CMAKE_COMMAND}"
    "-DROOT=${CMAKE_CURRENT_SOURCE_DIR}"
    "-DCXX=${CMAKE_CXX_COMPILER}"
    "-DSANITIZERS=${ATTO_FORGE_SANITIZERS}"
    "-DFORGE=$<TARGET_FILE:forge_scalar>"
    "-DOUT_DIR=${CMAKE_CURRENT_BINARY_DIR}/scalar-reproducibility"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/scalar_reproducibility.cmake")

# Parse-only admission without output side effects.
add_test(NAME scalar_check_only
  COMMAND "${CMAKE_COMMAND}"
    "-DROOT=${CMAKE_CURRENT_SOURCE_DIR}"
    "-DFORGE=$<TARGET_FILE:forge_scalar>"
    "-DOUT_DIR=${CMAKE_CURRENT_BINARY_DIR}/scalar-check"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/scalar_check.cmake")

# Bounded local Pointer<int> compiler-native guest parity. This runs real
# generated source under GCC/Clang and sanitizers, never a Python model.
add_executable(pointer_local_typed_test tests/pointer_local_typed.cpp)
target_link_libraries(pointer_local_typed_test PRIVATE atto_forge_checked_i64)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(pointer_local_typed_test PRIVATE -Wall -Wextra -Werror -pedantic -UNDEBUG)
  if(ATTO_FORGE_SANITIZERS)
    target_compile_options(pointer_local_typed_test PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(pointer_local_typed_test PRIVATE -fsanitize=address,undefined)
  endif()
elseif(MSVC)
  target_compile_options(pointer_local_typed_test PRIVATE /UNDEBUG)
endif()
add_test(NAME pointer_local_typed_test COMMAND pointer_local_typed_test)
add_test(NAME pointer_local_native_guests
  COMMAND "${CMAKE_COMMAND}"
    "-DROOT=${CMAKE_CURRENT_SOURCE_DIR}"
    "-DCXX=${CMAKE_CXX_COMPILER}"
    "-DSANITIZERS=${ATTO_FORGE_SANITIZERS}"
    "-DFORGE=$<TARGET_FILE:forge_scalar>"
    "-DOUT_DIR=${CMAKE_CURRENT_BINARY_DIR}/pointer-local-guests"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/pointer_local_guest.cmake")
