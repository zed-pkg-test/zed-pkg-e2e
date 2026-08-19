const UPLOAD_ARTIFACT_SHA = '043fb46d1a93c77aae656e7c1c64a875d1fc6a0a'; // v7

function replaceOnce(source, before, after, label) {
  const count = source.split(before).length - 1;
  if (count !== 1) {
    throw new Error(`${label}: expected one generated seam, found ${count}`);
  }
  return source.replace(before, after);
}

/**
 * Apply source-access semantics to the generated test-org harness.
 *
 * The generic generated workflow can prove that its protected credential gate
 * and generated plan ran. It cannot certify arbitrary product source without a
 * product-specific executable overlay, so its status remains explicitly not
 * certified even after a successful access/plan run.
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
    'Product-specific files outside the generated file set are preserved and must add executable assertions without weakening the base contract. Full integration is intentionally release-gated until required source repositories and organization read credentials are present. The generic protected lane reports source-access status only; source certification requires a product-specific executable overlay. A skipped integration job is not source certification.';
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
    "          printf 'protected source access enabled\\\\n'\\n",
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
    '    name: Record protected source access status\\n',
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
    "          const sourceAccessPassed = result === 'success';\\n",
    '          const report = {\\n',
    '            schemaVersion: 2,\\n',
    '            enabled,\\n',
    '            executed,\\n',
    '            sourceAccessPassed,\\n',
    '            certified: false,\\n',
    '            result,\\n',
    "            reason: sourceAccessPassed ? 'protected-source-access-passed-product-certification-required'\\n",
    "              : executed ? 'protected-source-access-did-not-pass'\\n",
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
    'protected integration source-access status',
  );

  return source;
}
