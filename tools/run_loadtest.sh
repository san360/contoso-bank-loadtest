#!/usr/bin/env sh
# =============================================================================
# Contoso Bank - run a provisioned Azure Load Testing definition
# =============================================================================
# `azd up` creates the test definitions but deliberately never runs them, because
# running spends virtual user hours. This script is the explicit "run it now" step.
#
# It resolves the Azure Load Testing resource from the azd environment (or from
# flags / environment variables), starts a run, polls until the run reaches a
# terminal state, and prints the status, the derived pass/fail verdict, the
# breached criteria and the headline client-side statistics.
#
# The verdict is derived from passFailCriteria.passFailMetrics on the run, not from
# the aggregate testResult field. Those two settle at different times - see
# docs/07-analyzing-results-and-bottlenecks.md section 7.2.
#
# Exit codes: 0 = completed with no failed criterion (or --list / --no-wait)
#             1 = run failed/cancelled, a criterion was breached, or an error
#             2 = the run did not finish within --timeout minutes
# =============================================================================

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
AZD_VALUES_LOADED=0
AZD_VALUES=""

TEST_ID=""
RUN_ID=""
PRESET=""
LIST_ONLY=0
NO_WAIT=0
DOWNLOAD=0
OUTPUT_DIR=""
TIMEOUT_MINUTES=40

MAX_RUN_ID_LENGTH=50

default_run_id() {
    # Azure Load Testing caps testRunId at 50 characters, so long test ids have to
    # give way to the timestamp - the timestamp is what makes the id unique.
    stamp=$(date -u +%Y%m%d-%H%M%S)
    budget=$((MAX_RUN_ID_LENGTH - ${#stamp} - 1))
    prefix=$1
    if [ ${#prefix} -gt "$budget" ]; then
        prefix=$(printf '%s' "$prefix" | cut -c1-"$budget")
    fi
    prefix=$(printf '%s' "$prefix" | sed 's/-*$//')
    printf '%s-%s' "$prefix" "$stamp"
}

usage() {
    cat <<'EOF'
Usage: tools/run_loadtest.sh [--test-id <id>] [options]

Options:
  -t, --test-id <id>     Test definition to run, e.g. contoso-bank-peak-load
  -r, --run-id <id>      Test run id (default "<testId>-YYYYmmdd-HHMMSS")
  -l, --list             List the available test definitions and exit
  -p, --preset <name>    POST 'bottlenecked' or 'optimized' to the demo app first
      --no-wait          Start the run and return immediately
      --timeout <mins>   How long to poll for completion (default 40)
      --download         Download results (CSV/JTL) and the JMeter HTML report
      --output <dir>     Where --download writes (default .loadtest-results/<runId>)
      --load-test <name> Override LOAD_TEST_NAME
      --resource-group <name> Override AZURE_RESOURCE_GROUP
      --subscription <id>     Override AZURE_SUBSCRIPTION_ID
  -h, --help             Show this help

Examples:
  tools/run_loadtest.sh --list
  tools/run_loadtest.sh --test-id contoso-bank-url-basic
  tools/run_loadtest.sh -t contoso-bank-pr-gate -p bottlenecked -r demo-run-before --download
EOF
}

fail() {
    printf '\nFailed to run the load test.\n%s\n' "$1" >&2
    exit 1
}

say_step() {
    printf '\n%s\n' "$1"
}

require_tool() {
    command -v "$1" >/dev/null 2>&1 || fail "'$1' is not on PATH. Install it before running this script."
}

load_azd_values() {
    if [ "$AZD_VALUES_LOADED" -eq 1 ]; then
        return
    fi
    AZD_VALUES_LOADED=1
    if command -v azd >/dev/null 2>&1; then
        AZD_VALUES=$(cd "$REPO_ROOT" && azd env get-values 2>/dev/null) || AZD_VALUES=""
    fi
}

get_env_value() {
    name=$1
    required=${2:-false}
    eval "value=\${$name:-}"
    if [ -z "$value" ]; then
        load_azd_values
        value=$(printf '%s\n' "$AZD_VALUES" | awk -F= -v key="$name" '$1 == key { v=$0; sub(/^[^=]*=/, "", v); gsub(/^"|"$/, "", v); print v; exit }')
    fi
    if [ "$required" = "true" ] && [ -z "$value" ]; then
        fail "Could not resolve '$name'. Run 'azd up' first, pass the matching flag, or export it as an environment variable."
    fi
    printf '%s' "$value"
}

ensure_prerequisites() {
    require_tool az
    az account show --only-show-errors -o none >/dev/null 2>&1 \
        || fail "Azure CLI is not logged in. Run 'az login' and retry."

    if ! az extension show --name load --only-show-errors -o none >/dev/null 2>&1; then
        printf "  Azure CLI 'load' extension is missing. Installing it now...\n"
        az extension add --name load --only-show-errors -o none \
            || fail "Could not install Azure CLI 'load' extension."
    fi
}

# az load <...> with the resource/group/subscription arguments already applied.
load_cli() {
    if [ -n "$SUBSCRIPTION_ID" ]; then
        az load "$@" --load-test-resource "$LOAD_TEST_NAME" --resource-group "$RESOURCE_GROUP" \
            --subscription "$SUBSCRIPTION_ID" --only-show-errors
    else
        az load "$@" --load-test-resource "$LOAD_TEST_NAME" --resource-group "$RESOURCE_GROUP" \
            --only-show-errors
    fi
}

local_test_ids() {
    if [ ! -d "$REPO_ROOT/loadtests/config" ]; then
        return
    fi
    for file in "$REPO_ROOT"/loadtests/config/*.yaml; do
        [ -f "$file" ] || continue
        sed -n 's/^[[:space:]]*testId:[[:space:]]*["'"'"']\{0,1\}\([^"'"'"'[:space:]#]*\).*/\1/p' "$file" | head -n 1
    done
}

show_catalog() {
    say_step "Test definitions in '$LOAD_TEST_NAME'"

    remote_ids=$(load_cli test list --query "[].testId" -o tsv 2>/dev/null || true)
    if [ -z "$remote_ids" ]; then
        printf '  Could not list tests from Azure; showing the definitions in loadtests/config only.\n'
    fi

    printf '  %-34s %s\n' 'TEST ID' 'DEPLOYED'
    local_ids=$(local_test_ids)
    printf '%s\n' "$local_ids" | while IFS= read -r id; do
        [ -n "$id" ] || continue
        if printf '%s\n' "$remote_ids" | grep -qx "$id"; then
            printf '  %-34s %s\n' "$id" 'yes'
        else
            printf '  %-34s %s\n' "$id" 'no'
        fi
    done
    printf '%s\n' "$remote_ids" | while IFS= read -r id; do
        [ -n "$id" ] || continue
        if ! printf '%s\n' "$local_ids" | grep -qx "$id"; then
            printf '  %-34s %s\n' "$id" 'yes (not in this repo)'
        fi
    done

    printf '\nRun one with:  tools/run_loadtest.sh --test-id <testId>\n'
    printf "Anything marked no needs 'tools/provision_loadtests.sh' first.\n"
}

set_app_preset() {
    require_tool curl
    api_uri=$(get_env_value SERVICE_API_URI)
    if [ -z "$api_uri" ]; then
        api_host=$(get_env_value SERVICE_API_HOSTNAME)
        [ -n "$api_host" ] || fail "--preset needs SERVICE_API_URI or SERVICE_API_HOSTNAME from the azd environment."
        api_uri="https://$api_host"
    fi
    api_uri=$(printf '%s' "$api_uri" | sed 's#/$##')

    say_step "Switching the demo app to the '$1' preset"
    curl -fsS -X POST "$api_uri/admin/config" -H 'Content-Type: application/json' \
        -d "{\"preset\":\"$1\"}" >/dev/null || fail "Could not set preset '$1' on $api_uri."
    printf '  [OK] %s now runs the %s preset.\n' "$api_uri" "$1"

    # App Service needs a moment before the new config is live on every worker.
    sleep 5
}

while [ $# -gt 0 ]; do
    case "$1" in
        -t|--test-id) TEST_ID=${2:?missing value for --test-id}; shift 2 ;;
        -r|--run-id) RUN_ID=${2:?missing value for --run-id}; shift 2 ;;
        -p|--preset) PRESET=${2:?missing value for --preset}; shift 2 ;;
        -l|--list) LIST_ONLY=1; shift ;;
        --no-wait) NO_WAIT=1; shift ;;
        --timeout) TIMEOUT_MINUTES=${2:?missing value for --timeout}; shift 2 ;;
        --download) DOWNLOAD=1; shift ;;
        --output) OUTPUT_DIR=${2:?missing value for --output}; shift 2 ;;
        --load-test) LOAD_TEST_NAME_OVERRIDE=${2:?missing value for --load-test}; shift 2 ;;
        --resource-group) RESOURCE_GROUP_OVERRIDE=${2:?missing value for --resource-group}; shift 2 ;;
        --subscription) SUBSCRIPTION_ID_OVERRIDE=${2:?missing value for --subscription}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage; fail "Unknown argument '$1'." ;;
    esac
done

case "$PRESET" in
    ''|bottlenecked|optimized) ;;
    *) fail "--preset must be 'bottlenecked' or 'optimized'." ;;
esac

ensure_prerequisites

LOAD_TEST_NAME=${LOAD_TEST_NAME_OVERRIDE:-$(get_env_value LOAD_TEST_NAME true)}
RESOURCE_GROUP=${RESOURCE_GROUP_OVERRIDE:-$(get_env_value AZURE_RESOURCE_GROUP true)}
SUBSCRIPTION_ID=${SUBSCRIPTION_ID_OVERRIDE:-$(get_env_value AZURE_SUBSCRIPTION_ID)}
LOAD_TEST_RESOURCE_ID=$(get_env_value LOAD_TEST_RESOURCE_ID)
if [ -z "$LOAD_TEST_RESOURCE_ID" ] && [ -n "$SUBSCRIPTION_ID" ]; then
    LOAD_TEST_RESOURCE_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.LoadTestService/loadTests/$LOAD_TEST_NAME"
fi

if [ "$LIST_ONLY" -eq 1 ] || [ -z "$TEST_ID" ]; then
    show_catalog
    if [ "$LIST_ONLY" -eq 0 ]; then
        printf '\nNo --test-id supplied, so nothing was run and no virtual user hours were spent.\n'
    fi
    exit 0
fi

load_cli test show --test-id "$TEST_ID" -o none >/dev/null 2>&1 \
    || fail "Test '$TEST_ID' does not exist in '$LOAD_TEST_NAME'. Run 'tools/provision_loadtests.sh' first, or use --list to see what is deployed."

if [ -n "$PRESET" ]; then
    set_app_preset "$PRESET"
fi

[ -n "$RUN_ID" ] || RUN_ID=$(default_run_id "$TEST_ID")

# The service rejects anything longer than 50 characters with a generic
# InvalidRequest. Fail here instead, where the message can say what to do.
if [ ${#RUN_ID} -gt "$MAX_RUN_ID_LENGTH" ] || [ ${#RUN_ID} -lt 2 ]; then
    fail "Run id '$RUN_ID' is ${#RUN_ID} characters. Azure Load Testing requires 2-$MAX_RUN_ID_LENGTH. Pass a shorter --run-id."
fi

say_step "Starting run '$RUN_ID' for test '$TEST_ID'"
printf "  This spends virtual user hours. Estimate the cost with 'python tools/vuh_calculator.py' if you need to.\n"
load_cli test-run create --test-id "$TEST_ID" --test-run-id "$RUN_ID" --no-wait >/dev/null \
    || fail "Could not start the test run."
printf '  [OK] Run accepted.\n'

if [ "$NO_WAIT" -eq 1 ]; then
    printf '\nNot waiting (--no-wait). Check progress with:\n'
    printf '  az load test-run show --load-test-resource %s --resource-group %s --test-run-id %s --query status -o tsv\n' \
        "$LOAD_TEST_NAME" "$RESOURCE_GROUP" "$RUN_ID"
    exit 0
fi

say_step "Waiting for the run to finish (timeout ${TIMEOUT_MINUTES}m)"
deadline=$(( $(date +%s) + TIMEOUT_MINUTES * 60 ))
status=""
last_status=""
while [ "$(date +%s)" -lt "$deadline" ]; do
    status=$(load_cli test-run show --test-run-id "$RUN_ID" --query status -o tsv 2>/dev/null || true)
    case "$status" in
        DONE|FAILED|CANCELLED) break ;;
    esac
    if [ -n "$status" ] && [ "$status" != "$last_status" ]; then
        printf '  %s...\n' "$status"
        last_status=$status
    fi
    status=""
    sleep 15
done

if [ -z "$status" ]; then
    printf '\nRun %s had not reached a terminal state after %s minute(s). It is still running in Azure.\n' "$RUN_ID" "$TIMEOUT_MINUTES" >&2
    exit 2
fi

# Wait for the verdict to SETTLE, not just for the run to finish.
#
# MEASURED, not assumed: at the moment status first flips to DONE this service
# reports testResult = NOT_APPLICABLE *and* every passFailMetrics entry still
# reads result = "passed". The criteria settle to their real values later. Run
# contoso-bank-pr-gate-runall-20260915-102506 read as 0/4 criteria failed at
# first DONE and later as 3/4 failed (avg 2725ms against a 500ms criterion).
# Reading either signal too early therefore produces a FALSE GREEN, which is the
# worst possible outcome for a gate. See docs/07 section 7.2.
settle_deadline=$(( $(date +%s) + SETTLE_TIMEOUT_SECONDS ))
settled=0
settle_start=$(date +%s)
announced=0

criteria_total=$(load_cli test-run show --test-run-id "$RUN_ID" \
    --query "length(values(passFailCriteria.passFailMetrics))" -o tsv 2>/dev/null || echo 0)
[ -n "$criteria_total" ] || criteria_total=0

if [ "$status" != "DONE" ] || [ "$criteria_total" -eq 0 ]; then
    # A criteria-less test never resolves past NOT_APPLICABLE, and a run that did
    # not complete has no verdict to wait for.
    settled=1
fi

reported_result=$(load_cli test-run show --test-run-id "$RUN_ID" --query testResult -o tsv 2>/dev/null || true)

while [ "$settled" -eq 0 ] && [ "$(date +%s)" -lt "$settle_deadline" ]; do
    case "$reported_result" in
        PASSED|FAILED) settled=1; break ;;
    esac
    if [ "$announced" -eq 0 ]; then
        printf '  DONE - waiting for the pass/fail verdict to settle...\n'
        announced=1
    fi
    sleep 10
    reported_result=$(load_cli test-run show --test-run-id "$RUN_ID" --query testResult -o tsv 2>/dev/null || true)
done

settle_seconds=$(( $(date +%s) - settle_start ))

criteria_total=$(load_cli test-run show --test-run-id "$RUN_ID" \
    --query "length(values(passFailCriteria.passFailMetrics))" -o tsv 2>/dev/null || echo 0)
criteria_failed=$(load_cli test-run show --test-run-id "$RUN_ID" \
    --query "length(values(passFailCriteria.passFailMetrics)[?result=='failed'])" -o tsv 2>/dev/null || echo 0)
[ -n "$criteria_total" ] || criteria_total=0
[ -n "$criteria_failed" ] || criteria_failed=0

# Take the pessimistic reading of the two signals: either one reporting failure
# is a failure, and anything short of positive proof of a pass is UNSETTLED.
if [ "$criteria_total" -eq 0 ]; then
    verdict="NOT_APPLICABLE"
elif [ "$status" != "DONE" ]; then
    verdict="NO_VERDICT"
elif [ "$settled" -eq 0 ]; then
    verdict="UNSETTLED"
elif [ "$criteria_failed" -gt 0 ] || [ "$reported_result" = "FAILED" ]; then
    verdict="FAILED"
elif [ "$reported_result" = "PASSED" ]; then
    verdict="PASSED"
else
    verdict="UNSETTLED"
fi

say_step "Run summary"
printf '  test id       : %s\n' "$TEST_ID"
printf '  run id        : %s\n' "$RUN_ID"
printf '  status        : %s\n' "$status"
printf '  verdict       : %s  (%s/%s criteria failed, testResult=%s)\n' "$verdict" "$criteria_failed" "$criteria_total" "$reported_result"
if [ "$settle_seconds" -gt 0 ]; then
    printf '  settled after : %ss past the terminal state\n' "$settle_seconds"
fi

if [ "$verdict" = "NO_VERDICT" ]; then
    printf '  warning       : the run did not complete, so there is no performance conclusion.\n'
    printf '                  status=%s means the test infrastructure, script, secrets or network path\n' "$status"
    printf '                  failed - your test is broken, not necessarily your app. Engine logs:\n'
    printf '                  az load test-run download-files --test-run-id %s --path ./logs --log --force\n' "$RUN_ID"
elif [ "$verdict" = "UNSETTLED" ]; then
    printf '  warning       : the verdict had not settled when this script gave up waiting.\n'
    printf '                  Re-read the run before trusting it - do NOT treat this as a pass.\n'
elif [ "$reported_result" = "FAILED" ] && [ "$criteria_failed" -eq 0 ]; then
    printf "  note          : testResult says FAILED while every criterion still reads 'passed'.\n"
    printf '                  The per-criterion results lag; the failing verdict is authoritative.\n'
fi

if [ "$criteria_total" -eq 0 ]; then
    printf '  warning       : this test defines no failure criteria, so it can never fail.\n'
    printf '                  A green run here proves nothing - see docs/07 section 7.2.\n'
fi

if [ "$criteria_failed" -gt 0 ]; then
    load_cli test-run show --test-run-id "$RUN_ID" \
        --query "values(passFailCriteria.passFailMetrics)[?result=='failed'].[aggregate,clientMetric,condition,value,actualValue]" \
        -o tsv 2>/dev/null | while IFS="$(printf '\t')" read -r aggregate metric condition value actual; do
            [ -n "$aggregate" ] || continue
            printf '  breached      : %s(%s) %s %s - actual %s\n' "$aggregate" "$metric" "$condition" "$value" "$actual"
        done
fi

stats=$(load_cli test-run show --test-run-id "$RUN_ID" \
    --query "testRunStatistics.Total.[sampleCount,errorCount,errorPct,throughput,meanResTime,pct1ResTime,pct2ResTime,pct3ResTime]" \
    -o tsv 2>/dev/null || true)
if [ -n "$stats" ]; then
    printf '%s\n' "$stats" | while IFS="$(printf '\t')" read -r samples errors error_pct throughput mean p90 p95 p99; do
        printf '\n  samples       : %s\n' "$samples"
        printf '  errors        : %s (%s%%)\n' "$errors" "$error_pct"
        printf '  throughput    : %s req/s\n' "$throughput"
        printf '  response ms   : mean %s / p90 %s / p95 %s / p99 %s\n' "$mean" "$p90" "$p95" "$p99"
    done
fi

if [ -n "$LOAD_TEST_RESOURCE_ID" ]; then
    printf '\n  portal        : https://portal.azure.com/#resource%s/tests\n' "$LOAD_TEST_RESOURCE_ID"
fi
printf '  cli           : az load test-run show --load-test-resource %s --resource-group %s --test-run-id %s\n' \
    "$LOAD_TEST_NAME" "$RESOURCE_GROUP" "$RUN_ID"

if [ "$DOWNLOAD" -eq 1 ]; then
    [ -n "$OUTPUT_DIR" ] || OUTPUT_DIR="$REPO_ROOT/.loadtest-results/$RUN_ID"
    say_step "Downloading results and report"
    mkdir -p "$OUTPUT_DIR"
    if load_cli test-run download-files --test-run-id "$RUN_ID" --path "$OUTPUT_DIR" --result --report --force >/dev/null 2>&1; then
        printf '  [OK] artefacts written to %s\n' "$OUTPUT_DIR"
    else
        printf '  Could not download the run artefacts.\n' >&2
    fi
fi

# Anything that is not an unambiguous pass exits non-zero. A gate that cannot
# prove the run passed must not report success.
case "$verdict" in
    FAILED|UNSETTLED|NO_VERDICT) exit 1 ;;
esac
if [ "$status" != "DONE" ]; then
    exit 1
fi
exit 0
