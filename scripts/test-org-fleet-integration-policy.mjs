const UPLOAD_ARTIFACT_SHA = '043fb46d1a93c77aae656e7c1c64a875d1fc6a0a'; // v7

function replaceOnce(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count !== 1) {
    throw new Error(`${label}: expected one generated seam, found ${count}`);
  }
  return source.replace(before, after);
}

/**
 * Apply source-certification semantics to the generated test-org harness.
 *
 * The large generator is stored as split source parts. Keeping this policy in a
 * small imported module makes the security boundary reviewable and unit-testable
 * without rewriting the generated fleet payload.
 */
export function hardenGeneratedIntegrationPolicy(input) {
  let source = input;

  const planSecurityBefore = [
    '    security: {\n',
    '      immutableSourcePins: true,\n',
    '      pullRequestCredentials: false,\n',
    '      leastPrivilege: true,\n',
    '      redactSecretsAndRawMedia: true,\n',
    '    },',
  ].join('');
  const planSecurityAfter = [
    '    security: {\n',
    '      immutableSourcePins: true,\n',
    '      pullRequestCredentials: false,\n',
    '      leastPrivilege: true,\n',
    '      redactSecretsAndRawMedia: true,\n',
    '      protectedSourceIntegration: true,\n',
    "      protectedSourceIntegrationStatus: 'not-executed-unless-protected-lane-runs',\n",
    '      productOverlayPreserved: true,\n',
    '    },',
  ].join('');
  source = replaceOnce(
    source,
    planSecurityBefore,
    planSecurityAfter,
    'generated plan security contract',
  );

  const readmeBefore =
    'Full integration is intentionally release-gated until required source repositories and organization read credentials are present.';
  const readmeAfter =
    'Product-specific files outside the generated file set are preserved and must add executable assertions without weakening the base contract. Full integration is intentionally release-gated until required source repositories and organization read credentials are present. A skipped integration job is not source certification; inspect the source-integration status artifact.';
  source = replaceOnce(
    source,
    readmeBefore,
    readmeAfter,
    'generated README integration boundary',
  );

  source = replaceOnce(
    source,
    '          token: \\${{ secrets.TEST_FLEET_READ_TOKEN || github.token }}',
    '          token: \\${{ secrets.TEST_FLEET_READ_TOKEN }}',
    'protected checkout token fallback',
  );

  const stepsBefore =
    '    steps:\\n      - name: Check out source pins and submodules\\n';
  const stepsAfter = [
    '    steps:\\n',
    '      - name: Require protected cross-organization read credential\\n',
    '        env:\\n',
    '          TEST_FLEET_READ_TOKEN: \\${{ secrets.TEST_FLEET_READ_TOKEN }}\\n',
    '        run: |\\n',
    '          set -euo pipefail\\n',
    '          test -n "$TEST_FLEET_READ_TOKEN"\\n',
    "          printf 'protected source integration enabled\\\\n'\\n",
    '      - name: Check out source pins and submodules\\n',
  ].join('');
  source = replaceOnce(
    source,
    stepsBefore,
    stepsAfter,
    'protected integration credential requirement',
  );

  const tailBefore = [
    '      - name: Explain profile gate\\n',
    '        run: |\\n',
    '          echo "Profile: ${spec.profile}"\\n',
    '          echo "Required checks are defined in test-plan.json."\\n',
    '          echo "Add product-specific executable fixtures here without weakening the generated contract."\\n',
    '`;',
  ].join('');
  const tailAfter = [
    '      - name: Explain profile gate\\n',
    '        run: |\\n',
    '          echo "Profile: ${spec.profile}"\\n',
    '          echo "Required checks are defined in test-plan.json."\\n',
    '          echo "Add product-specific executable fixtures here without weakening the generated contract."\\n',
    '\\n',
    '  certification-status:\\n',
    '    name: Record protected source certification status\\n',
    '    if: always()\\n',
    '    needs: integration\\n',
    '    runs-on: ubuntu-24.04\\n',
    '    timeout-minutes: 5\\n',
    '    steps:\\n',
    '      - name: Write source-integration status\\n',
    '        env:\\n',
    '          ENABLED: \\${{ vars.ENABLE_TEST_FLEET_INTEGRATION }}\\n',
    '          RESULT: \\${{ needs.integration.result }}\\n',
    '        run: |\\n',
    "          node - <<'NODE'\\n",
    "          const fs = require('node:fs');\\n",
    "          const result = process.env.RESULT || 'unknown';\\n",
    "          const enabled = process.env.ENABLED === 'true';\\n",
    "          const executed = result !== 'skipped';\\n",
    "          const certified = result === 'success';\\n",
    '          const report = {\\n',
    '            schemaVersion: 1,\\n',
    '            enabled,\\n',
    '            executed,\\n',
    '            certified,\\n',
    '            result,\\n',
    "            reason: certified ? 'protected-source-integration-passed'\\n",
    "              : executed ? 'protected-source-integration-did-not-pass'\\n",
    "                : 'protected-source-integration-not-enabled',\\n",
    '          };\\n',
    "          fs.writeFileSync('source-integration-status.json', JSON.stringify(report, null, 2) + '\\\\n');\\n",
    "          console.log(JSON.stringify(report));\\n",
    '          NODE\\n',
    '      - name: Upload source-integration status\\n',
    `        uses: actions/upload-artifact@${UPLOAD_ARTIFACT_SHA} # v7\\n`,
    '        with:\\n',
    '          name: source-integration-status-\\${{ github.sha }}\\n',
    '          path: source-integration-status.json\\n',
    '          if-no-files-found: error\\n',
    '          retention-days: 14\\n',
    '`;',
  ].join('');
  source = replaceOnce(
    source,
    tailBefore,
    tailAfter,
    'protected integration certification status',
  );

  return source;
}
