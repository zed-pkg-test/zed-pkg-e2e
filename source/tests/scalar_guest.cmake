# Executed restricted .atto -> native C++ -> binary guest verification.
if(NOT DEFINED ROOT OR NOT DEFINED FORGE OR NOT DEFINED CXX OR NOT DEFINED OUT_DIR)
  message(FATAL_ERROR "missing scalar guest integration variables")
endif()
set(guest_flags)
if(SANITIZERS)
  list(APPEND guest_flags -fsanitize=address,undefined -fno-omit-frame-pointer)
endif()
file(MAKE_DIRECTORY "${OUT_DIR}")
function(verify_guest fixture want_rc want_out want_err)
  set(source "${ROOT}/fixtures/scalar-v1/${fixture}.atto")
  set(cpp "${OUT_DIR}/${fixture}.cpp")
  set(binary "${OUT_DIR}/${fixture}")
  execute_process(COMMAND "${FORGE}" "${source}" -o "${cpp}"
    RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
  if(NOT "${rc}" STREQUAL "0")
    message(FATAL_ERROR "${fixture}: frontend failed: ${rc}: ${err}")
  endif()
  execute_process(COMMAND "${CXX}" -std=c++20 -Wall -Wextra -Werror
    ${guest_flags} "-I${ROOT}/include" "${cpp}" -o "${binary}"
    RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
  if(NOT "${rc}" STREQUAL "0")
    message(FATAL_ERROR "${fixture}: generated C++ build failed: ${err}")
  endif()
  execute_process(COMMAND "${binary}"
    RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
  if(NOT "${rc}" STREQUAL "${want_rc}" OR
     NOT "${out}" STREQUAL "${want_out}" OR
     NOT "${err}" STREQUAL "${want_err}")
    message(FATAL_ERROR "${fixture}: compiled guest returned ${rc}, stdout=[${out}], stderr=[${err}]")
  endif()
endfunction()
verify_guest(arithmetic "0" "34\n" "")
verify_guest(overflow "70" "" "E_OVERFLOW\n")
verify_guest(divzero "71" "" "E_DIVZERO\n")
verify_guest(minimum "0" "-9223372036854775808\n" "")

set(invalid "${OUT_DIR}/invalid.atto")
set(stale "${OUT_DIR}/invalid.cpp")
file(WRITE "${invalid}" "fnc main(): int {return os.read_env(\"HOME\");}")
file(WRITE "${stale}" "stale source")
execute_process(COMMAND "${FORGE}" "${invalid}" -o "${stale}"
  RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
file(READ "${stale}" after_invalid)
if(NOT "${rc}" STREQUAL "2" OR NOT "${after_invalid}" STREQUAL "stale source")
  message(FATAL_ERROR "invalid source deleted or changed existing output")
endif()
string(REPEAT "1" 17000 huge)
file(WRITE "${OUT_DIR}/huge.atto" "${huge}")
execute_process(COMMAND "${FORGE}" "${OUT_DIR}/huge.atto" -o "${stale}"
  RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
file(READ "${stale}" after_size_reject)
if(NOT "${rc}" STREQUAL "2" OR NOT "${after_size_reject}" STREQUAL "stale source")
  message(FATAL_ERROR "oversized source deleted or changed existing output")
endif()

# Guard output and temp-path direct symlink clobber.
set(protected_file "${OUT_DIR}/protected.txt")
file(WRITE "${protected_file}" "do not overwrite")
set(link_input "${OUT_DIR}/symlink-input.atto")
file(WRITE "${link_input}" "fnc main(): int {return 7;}")
set(link_output "${OUT_DIR}/symlink-output.cpp")
set(link_temp "${OUT_DIR}/symlink-output.cpp.part")
file(CREATE_LINK "${protected_file}" "${link_temp}" SYMBOLIC RESULT link_result)
if(NOT "${link_result}" STREQUAL "0")
  message(FATAL_ERROR "cannot establish temporary symlink regression")
endif()
execute_process(COMMAND "${FORGE}" "${link_input}" -o "${link_output}"
  RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
file(READ "${protected_file}" protected_bytes)
if(NOT "${rc}" STREQUAL "2" OR
   NOT "${protected_bytes}" STREQUAL "do not overwrite")
  message(FATAL_ERROR "temporary symlink redirected native source write")
endif()
file(REMOVE "${link_temp}")
file(CREATE_LINK "${OUT_DIR}/does-not-exist" "${link_output}"
  SYMBOLIC RESULT link_result)
if(NOT "${link_result}" STREQUAL "0")
  message(FATAL_ERROR "cannot establish dangling symlink regression")
endif()
execute_process(COMMAND "${FORGE}" "${link_input}" -o "${link_output}"
  RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
if(NOT "${rc}" STREQUAL "2" OR NOT IS_SYMLINK "${link_output}")
  message(FATAL_ERROR "dangling output symlink was not rejected")
endif()
file(REMOVE "${link_output}")
file(WRITE "${link_temp}" "do not overwrite")
execute_process(COMMAND "${FORGE}" "${link_input}" -o "${link_output}"
  RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
file(READ "${link_temp}" tmp_bytes)
if(NOT "${rc}" STREQUAL "2" OR NOT "${tmp_bytes}" STREQUAL "do not overwrite")
  message(FATAL_ERROR "pre-existing temporary file was overwritten")
endif()
file(REMOVE "${link_temp}")

verify_guest(bitwise "0" "15\n" "")
verify_guest(bitwise_precedence "0" "1\n" "")

verify_guest(shift_logical "0" "9223372036854775807\n" "")
verify_guest(shift_arithmetic "0" "-1\n" "")
verify_guest(shift_masked "0" "1\n" "")
verify_guest(shift_negative_count "0" "-9223372036854775808\n" "")

verify_guest(minimum_padded "0" "-9223372036854775808\n" "")

# Reject symlinked parent directories; the target must not receive generated code.
set(target_dir "${OUT_DIR}/parent-target")
set(alias_dir "${OUT_DIR}/alias-parent")
file(MAKE_DIRECTORY "${target_dir}")
file(CREATE_LINK "${target_dir}" "${alias_dir}" SYMBOLIC RESULT link_result)
if(NOT "${link_result}" STREQUAL "0")
  message(FATAL_ERROR "cannot create symlink-parent regression")
endif()
set(alias_output "${alias_dir}/redirected.cpp")
execute_process(COMMAND "${FORGE}" "${link_input}" -o "${alias_output}"
  RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
if(NOT "${rc}" STREQUAL "2" OR EXISTS "${target_dir}/redirected.cpp")
  message(FATAL_ERROR "parent-symlink path admitted")
endif()
file(REMOVE "${alias_dir}")
execute_process(COMMAND "${FORGE}" "${link_input}" -o "${OUT_DIR}/missing-parent/file.cpp"
  RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
if(NOT "${rc}" STREQUAL "2")
  message(FATAL_ERROR "nonexistent output directory was not rejected")
endif()

verify_guest(comments "0" "14\n" "")

verify_guest(return_arrow "0" "42\n" "")
verify_guest(return_block_arrow "0" "42\n" "")
verify_guest(return_prefix "0" "42\n" "")
verify_guest(return_bare_prefix "0" "42\n" "")

verify_guest(depth_boundary "0" "42\n" "")
verify_guest(unary_depth_boundary "0" "42\n" "")

verify_guest(infer_colon "0" "42\n" "")
verify_guest(infer_arrow "0" "42\n" "")
verify_guest(infer_block "0" "42\n" "")
verify_guest(infer_prefix "0" "42\n" "")
verify_guest(infer_bare_prefix "0" "42\n" "")
