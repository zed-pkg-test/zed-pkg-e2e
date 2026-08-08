# Dedicated IDE repository certification

Tracking: Linear `DEN-2508`.

This lane independently certifies the first reviewable follow-up in each promoted IDE repository. Product repositories remain the implementation source; `zed-pkg-test/zed-pkg-e2e` pins immutable full commit identities and does not mutate product state.

| Integration | Product PR | Immutable head | Independent gate |
| --- | --- | --- | --- |
| VS Code | `zed-pkg/zed-vscode#1` | `9754f10e44235828547cbeeb05e43c5786673af9` | Node tests, repository contract, VSIX package |
| Qt Creator | `zed-pkg/zed-qtcreator#1` | `0372ccd4100e369d9e4593d3df66d8b2b507886a` | CMake build + CTest |
| Xcode | `zed-pkg/zed-xcode#1` | `654f96f9c1d3ee80afc3034a883cb9083caefe00` | Swift tests + release build on macOS |
| Eclipse | `zed-pkg/zed-eclipse#1` | `e65e8086f328a20adf3f941e1be988d0d757dc0f` | Java 21 Maven/JUnit |
| Visual Studio | `zed-pkg/zed-visual-studio#1` | `a3520ea8aa19ecbdaa4c71e77d01af6407a7e05f` | .NET 8 tests on Windows |

The gate proves repository-local code and controller/model contracts. It does not overclaim vendor GUI-instance, signing, or marketplace evidence. Those remain tracked by DEN-2508 and the per-repository conformance records.
