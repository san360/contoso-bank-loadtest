#!/usr/bin/env sh
# =============================================================================
# Contoso Bank - Azure Load Testing definitions hook
# =============================================================================
# `azd up` should leave the customer with a live app AND a populated Load Testing
# resource. This hook runs after deploy, retargets the versioned test artefacts to
# the generated App Service hostname, then creates or updates every YAML test
# definition under loadtests/config.
#
# It deliberately does not start a test run. Creating definitions is free;
# running them spends virtual user hours and stays an explicit presenter step.
# =============================================================================

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
AZD_VALUES_LOADED=0
AZD_VALUES=""
SUMMARY_ROWS=""

say_step() {
    printf '\n%s\n' "$1"
}

fail() {
    printf '\nFailed to provision Azure Load Testing definitions.\n%s\n' "$1" >&2
    exit 1
}

require_tool() {
    command -v "$1" >/dev/null 2>&1 || fail "'$1' is not on PATH. Install it before running this hook."
}

load_azd_values() {
    if [ "$AZD_VALUES_LOADED" -eq 1 ]; then
        return
    fi
    require_tool azd
    AZD_VALUES=$(cd "$REPO_ROOT" && azd env get-values) || fail "Could not read the azd environment. Run 'azd up' first, or export the required hook variables."
    AZD_VALUES_LOADED=1
}

get_hook_value() {
    name=$1
    required=${2:-false}
    eval "value=\${$name:-}"
    if [ -z "$value" ]; then
        load_azd_values
        value=$(printf '%s\n' "$AZD_VALUES" | awk -F= -v key="$name" '$1 == key { v=$0; sub(/^[^=]*=/, "", v); gsub(/^"|"$/, "", v); print v; exit }')
    fi
    if [ "$required" = "true" ] && [ -z "$value" ]; then
        fail "Required azd output '$name' is missing from the hook environment."
    fi
    printf '%s' "$value"
}

relative_path() {
    case "$1" in
        "$REPO_ROOT"/*) printf '%s' "${1#"$REPO_ROOT"/}" ;;
        *) printf '%s' "$1" ;;
    esac
}

normalize_host() {
    printf '%s' "$1" | sed -e 's#^[Hh][Tt][Tt][Pp][Ss]*://##' -e 's#/$##'
}

ensure_prerequisites() {
    say_step "Checking Azure CLI prerequisites"
    require_tool az
    az account show --only-show-errors -o none >/dev/null 2>&1 || fail "Azure CLI is not logged in. Run 'az login' and retry."
    printf '  [OK] Azure CLI is installed and logged in.\n'

    if az extension show --name load --only-show-errors -o none >/dev/null 2>&1; then
        printf "  [OK] Azure CLI 'load' extension is installed.\n"
    else
        printf "  Azure CLI 'load' extension is missing. Installing it now...\n"
        az extension add --name load --only-show-errors -o none || fail "Could not install Azure CLI 'load' extension."
        printf "  [OK] Azure CLI 'load' extension installed.\n"
    fi
}

retarget_native() {
    target_host=$1
    scratch="$REPO_ROOT/.azd-hooks"
    mkdir -p "$scratch"
    changed=0
    hits=0

    find "$REPO_ROOT/loadtests" -type f \( -name '*.yaml' -o -name '*.yml' -o -name '*.json' -o -name '*.conf' -o -name '*.properties' -o -name '*.jmx' \) | sort |
    while IFS= read -r file; do
        tmp="$scratch/retarget.$$.$(basename "$file")"
        count=$(awk -v h="$target_host" '
            {
                line=$0
                while (match(line, /[A-Za-z0-9][A-Za-z0-9-]*[.]azurewebsites[.]net/)) {
                    m=substr(line, RSTART, RLENGTH)
                    if (tolower(m) != tolower(h)) c++
                    line=substr(line, RSTART + RLENGTH)
                }
            }
            END { print c + 0 }
        ' "$file")

        awk -v h="$target_host" '{ gsub(/[A-Za-z0-9][A-Za-z0-9-]*[.]azurewebsites[.]net/, h); print }' "$file" > "$tmp"
        if cmp -s "$file" "$tmp"; then
            rm -f "$tmp"
        else
            mv "$tmp" "$file"
            changed=$((changed + 1))
            hits=$((hits + count))
            printf '  ->  %s  (%s occurrence%s updated by POSIX fallback)\n' "$(relative_path "$file")" "$count" "$(if [ "$count" = "1" ]; then printf ''; else printf 's'; fi)"
        fi
    done

    remaining=$(find "$scratch" -type f -name "retarget.$$.*" 2>/dev/null | head -n 1)
    [ -z "$remaining" ] || rm -f "$scratch"/retarget.$$.* 2>/dev/null || true

    # The loop above may run in a subshell on some /bin/sh implementations, so
    # use a fresh scan for the user-facing "nothing left" message.
    leftovers=$(find "$REPO_ROOT/loadtests" -type f \( -name '*.yaml' -o -name '*.yml' -o -name '*.json' -o -name '*.conf' -o -name '*.properties' -o -name '*.jmx' \) -exec grep -Eil '[A-Za-z0-9][A-Za-z0-9-]*\.azurewebsites\.net' {} \; | while IFS= read -r f; do
        grep -Eio '[A-Za-z0-9][A-Za-z0-9-]*\.azurewebsites\.net' "$f" | grep -ivx "$target_host" || true
    done | wc -l | tr -d ' ')
    if [ "$leftovers" = "0" ]; then
        printf '  =  no remaining load-test hostnames needed POSIX fallback changes\n'
    fi
}

retarget_artifacts() {
    service_host=$1
    target_host=$(normalize_host "$service_host")

    case "$target_host" in
        *.*) ;;
        *) fail "SERVICE_API_HOSTNAME '$service_host' does not look like a hostname." ;;
    esac

    say_step "Retargeting load test artefacts to $target_host"
    if command -v python3 >/dev/null 2>&1; then
        (cd "$REPO_ROOT" && python3 tools/set_target_host.py "$target_host") || fail "Python retargeting failed."
        printf '\nChecking for any newly-added artefacts not listed in set_target_host.py...\n'
    elif command -v python >/dev/null 2>&1; then
        (cd "$REPO_ROOT" && python tools/set_target_host.py "$target_host") || fail "Python retargeting failed."
        printf '\nChecking for any newly-added artefacts not listed in set_target_host.py...\n'
    else
        printf '  Python is not on PATH. Using the native POSIX fallback promised by azure.yaml.\n'
    fi
    retarget_native "$target_host"
}

retarget_keyvault() {
    vault_name=$1
    [ -n "$vault_name" ] || return 0

    say_step "Retargeting Key Vault secret references to $vault_name"

    # Deliberately drops any trailing /<version> segment. A pinned version dies the
    # moment the vault is recreated; the version-less form always resolves to the
    # current secret.
    kv_hits=0
    for file in $(find "$REPO_ROOT/loadtests" -type f \( -name '*.yaml' -o -name '*.yml' -o -name '*.json' \) 2>/dev/null); do
        grep -Eq 'https://[a-z0-9-]+\.vault\.azure\.net/secrets/' "$file" || continue
        tmp="$file.kvtmp.$$"
        sed -E "s#https://[a-z0-9-]+\.vault\.azure\.net/secrets/([A-Za-z0-9-]+)(/[0-9a-fA-F]+)?#https://${vault_name}.vault.azure.net/secrets/\1#g" "$file" > "$tmp"
        if cmp -s "$file" "$tmp"; then
            rm -f "$tmp"
        else
            mv "$tmp" "$file"
            kv_hits=$((kv_hits + 1))
            printf '  ->  %s  (secret URI updated)\n' "$(relative_path "$file")"
        fi
    done

    if [ "$kv_hits" = "0" ]; then
        printf '  =  no Key Vault secret URIs needed updating\n'
    fi
}

read_yaml_value() {
    file=$1
    key=$2
    awk -F: -v k="$key" '
        $1 ~ "^[[:space:]]*" k "[[:space:]]*$" {
            v=$0
            sub(/^[^:]*:[[:space:]]*/, "", v)
            sub(/[[:space:]]+#.*/, "", v)
            gsub(/^[[:space:]"'\''"]+|[[:space:]"'\''"]+$/, "", v)
            print v
            exit
        }
    ' "$file"
}

already_exists() {
    printf '%s' "$1" | grep -Eiq 'already[[:space:]]+exists|already[[:space:]]+exist|conflict|409'
}

az_load_args() {
    printf '%s\n' \
        --load-test-resource "$LOAD_TEST_NAME" \
        --resource-group "$AZURE_RESOURCE_GROUP" \
        --test-id "$1" \
        --load-test-config-file "$2" \
        --only-show-errors \
        -o none
    if [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
        printf '%s\n' --subscription "$AZURE_SUBSCRIPTION_ID"
    fi
}

show_query() {
    test_id=$1
    query=$2
    if [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
        az load test show --load-test-resource "$LOAD_TEST_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --test-id "$test_id" --subscription "$AZURE_SUBSCRIPTION_ID" --query "$query" --only-show-errors -o tsv 2>/dev/null || true
    else
        az load test show --load-test-resource "$LOAD_TEST_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --test-id "$test_id" --query "$query" --only-show-errors -o tsv 2>/dev/null || true
    fi
}

validation_status() {
    test_id=$1
    script_status=$(show_query "$test_id" 'inputArtifacts.testScriptFileInfo.validationStatus')
    url_status=$(show_query "$test_id" 'inputArtifacts.urlTestConfigFileInfo.validationStatus')

    if [ "$script_status" = "VALIDATION_FAILURE" ] || [ "$url_status" = "VALIDATION_FAILURE" ]; then
        printf 'VALIDATION_FAILURE'
    elif [ "$script_status" = "VALIDATION_SUCCESS" ] && { [ -z "$url_status" ] || [ "$url_status" = "VALIDATION_SUCCESS" ] || [ "$url_status" = "None" ]; }; then
        printf 'VALIDATION_SUCCESS'
    elif [ -n "$script_status$url_status" ]; then
        printf '%s%s%s' "$script_status" "$(if [ -n "$script_status" ] && [ -n "$url_status" ]; then printf ','; fi)" "$url_status"
    else
        printf 'UNKNOWN'
    fi
}

resource_property() {
    resource_id=$1
    property=$2
    if [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
        az resource show --ids "$resource_id" --subscription "$AZURE_SUBSCRIPTION_ID" --query "$property" --only-show-errors -o tsv
    else
        az resource show --ids "$resource_id" --query "$property" --only-show-errors -o tsv
    fi
}

component_json() {
    test_id=$1
    set +e
    if [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
        out=$(az load test app-component list --load-test-resource "$LOAD_TEST_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --test-id "$test_id" --subscription "$AZURE_SUBSCRIPTION_ID" --only-show-errors -o json 2>&1)
    else
        out=$(az load test app-component list --load-test-resource "$LOAD_TEST_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --test-id "$test_id" --only-show-errors -o json 2>&1)
    fi
    code=$?
    set -e
    if [ "$code" -eq 0 ]; then
        printf '%s' "$out"
    elif printf '%s' "$out" | grep -Eiq 'notfound|not found|not.*config'; then
        printf ''
    else
        fail "Could not list app components for test '$test_id'.\n$out"
    fi
}

server_metric_json() {
    test_id=$1
    set +e
    if [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
        out=$(az load test server-metric list --load-test-resource "$LOAD_TEST_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --test-id "$test_id" --subscription "$AZURE_SUBSCRIPTION_ID" --only-show-errors -o json 2>&1)
    else
        out=$(az load test server-metric list --load-test-resource "$LOAD_TEST_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --test-id "$test_id" --only-show-errors -o json 2>&1)
    fi
    code=$?
    set -e
    if [ "$code" -eq 0 ]; then
        printf '%s' "$out"
    elif printf '%s' "$out" | grep -Eiq 'notfound|not found|not.*config'; then
        printf ''
    else
        fail "Could not list server metrics for test '$test_id'.\n$out"
    fi
}

json_contains() {
    haystack=$1
    needle=$2
    printf '%s' "$haystack" | grep -Fiq "$needle"
}

encode_metric_name() {
    printf '%s' "$1" | sed -e 's#/#%2F#g' -e 's# #%20#g'
}

add_app_component_if_missing() {
    test_id=$1
    existing=$2
    component_id=$3
    component_name=$4
    component_type=$5
    component_kind=$6

    if json_contains "$existing" "$component_id"; then
        return 1
    fi

    args="--load-test-resource $LOAD_TEST_NAME --resource-group $AZURE_RESOURCE_GROUP --test-id $test_id --app-component-id $component_id --app-component-name $component_name --app-component-type $component_type --only-show-errors -o none"
    if [ -n "$component_kind" ] && [ "$component_kind" != "null" ]; then
        args="$args --app-component-kind $component_kind"
    fi
    if [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
        args="$args --subscription $AZURE_SUBSCRIPTION_ID"
    fi

    set +e
    out=$(az load test app-component add $args 2>&1)
    code=$?
    set -e
    if [ "$code" -ne 0 ] && ! already_exists "$out"; then
        fail "Could not add app component '$component_id' to test '$test_id'.\n$out"
    fi
    return 0
}

add_server_metric_if_missing() {
    test_id=$1
    existing=$2
    component_id=$3
    component_type=$4
    metric_name=$5
    metric_namespace=$6
    aggregation=$7
    metric_id="${component_id}/providers/microsoft.insights/metricdefinitions/$(encode_metric_name "$metric_name")"
    raw_metric_id="${component_id}/providers/microsoft.insights/metricdefinitions/${metric_name}"

    if json_contains "$existing" "$metric_id" || json_contains "$existing" "$raw_metric_id"; then
        return 1
    fi

    args="--load-test-resource $LOAD_TEST_NAME --resource-group $AZURE_RESOURCE_GROUP --test-id $test_id --app-component-id $component_id --app-component-type $component_type --metric-id $metric_id --metric-name $metric_name --metric-namespace $metric_namespace --aggregation $aggregation --only-show-errors -o none"
    if [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
        args="$args --subscription $AZURE_SUBSCRIPTION_ID"
    fi

    set +e
    out=$(az load test server-metric add $args 2>&1)
    code=$?
    set -e
    if [ "$code" -ne 0 ] && ! already_exists "$out"; then
        # Cross-platform parity with provision_loadtests.ps1.
        #
        # Some builds of the az load extension reject Azure Monitor metric
        # definition IDs whose metric name contains a slash (requests/duration,
        # performanceCounters/processCpuPercentage, ...) because the encoded %2F
        # fails their resource-ID validation. That is a CLI bug, not a config
        # error, and the metric is optional colour on the dashboard.
        #
        # This must not be fatal. The postdeploy hook runs with
        # continueOnError: false, so aborting here would fail the entire
        # `azd up` on Linux and on the ubuntu CI runner while the Windows
        # script merely warned and carried on - the classic "works on the
        # presenter's laptop, breaks in the pipeline" split.
        case "$metric_name" in
            */*)
                if printf '%s' "$out" | grep -q 'metric-id is not a valid Azure Resource ID'; then
                    printf '    [WARN] Skipping server metric %s for test %s: this az load build rejects the Azure Monitor metric definition ID.\n' "$metric_name" "$test_id" >&2
                    return 1
                fi
                ;;
        esac
        fail "Could not add server metric '$metric_name' to test '$test_id'.\n$out"
    fi
    return 0
}

configure_server_monitoring() {
    say_step "Configuring server-side metrics for diagnostic tests"

    app_name=$(resource_property "$SERVICE_API_RESOURCE_ID" name)
    app_type=$(resource_property "$SERVICE_API_RESOURCE_ID" type)
    app_kind=$(resource_property "$SERVICE_API_RESOURCE_ID" kind || true)
    appi_name=$(resource_property "$APPLICATIONINSIGHTS_RESOURCE_ID" name)
    appi_type=$(resource_property "$APPLICATIONINSIGHTS_RESOURCE_ID" type)
    appi_kind=$(resource_property "$APPLICATIONINSIGHTS_RESOURCE_ID" kind || true)
    plan_name=$(resource_property "$APP_SERVICE_PLAN_RESOURCE_ID" name)
    plan_type=$(resource_property "$APP_SERVICE_PLAN_RESOURCE_ID" type)
    plan_kind=$(resource_property "$APP_SERVICE_PLAN_RESOURCE_ID" kind || true)

    for test_id in contoso-bank-peak-load contoso-bank-peak-load-locust contoso-bank-pr-gate; do
        printf "  Ensuring App Components and metrics on '%s'...\n" "$test_id"

        existing_components=$(component_json "$test_id")
        components_added=0
        add_app_component_if_missing "$test_id" "$existing_components" "$SERVICE_API_RESOURCE_ID" "$app_name" "$app_type" "$app_kind" && components_added=$((components_added + 1)) || true
        add_app_component_if_missing "$test_id" "$existing_components" "$APPLICATIONINSIGHTS_RESOURCE_ID" "$appi_name" "$appi_type" "$appi_kind" && components_added=$((components_added + 1)) || true
        add_app_component_if_missing "$test_id" "$existing_components" "$APP_SERVICE_PLAN_RESOURCE_ID" "$plan_name" "$plan_type" "$plan_kind" && components_added=$((components_added + 1)) || true

        existing_metrics=$(server_metric_json "$test_id")
        metrics_added=0
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$APP_SERVICE_PLAN_RESOURCE_ID" "$plan_type" "CpuPercentage" "Microsoft.Web/serverfarms" "Average" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$APP_SERVICE_PLAN_RESOURCE_ID" "$plan_type" "MemoryPercentage" "Microsoft.Web/serverfarms" "Average" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$APP_SERVICE_PLAN_RESOURCE_ID" "$plan_type" "HttpQueueLength" "Microsoft.Web/serverfarms" "Maximum" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$SERVICE_API_RESOURCE_ID" "$app_type" "HttpResponseTime" "Microsoft.Web/sites" "Average" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$SERVICE_API_RESOURCE_ID" "$app_type" "Requests" "Microsoft.Web/sites" "Total" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$SERVICE_API_RESOURCE_ID" "$app_type" "Http5xx" "Microsoft.Web/sites" "Total" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$SERVICE_API_RESOURCE_ID" "$app_type" "MemoryWorkingSet" "Microsoft.Web/sites" "Average" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$SERVICE_API_RESOURCE_ID" "$app_type" "InstanceCount" "Microsoft.Web/sites" "Average" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$APPLICATIONINSIGHTS_RESOURCE_ID" "$appi_type" "requests/duration" "microsoft.insights/components" "Average" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$APPLICATIONINSIGHTS_RESOURCE_ID" "$appi_type" "requests/count" "microsoft.insights/components" "Count" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$APPLICATIONINSIGHTS_RESOURCE_ID" "$appi_type" "requests/failed" "microsoft.insights/components" "Count" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$APPLICATIONINSIGHTS_RESOURCE_ID" "$appi_type" "requests/rate" "microsoft.insights/components" "Average" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$APPLICATIONINSIGHTS_RESOURCE_ID" "$appi_type" "performanceCounters/requestsInQueue" "microsoft.insights/components" "Maximum" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$APPLICATIONINSIGHTS_RESOURCE_ID" "$appi_type" "performanceCounters/processCpuPercentage" "microsoft.insights/components" "Average" && metrics_added=$((metrics_added + 1)) || true
        add_server_metric_if_missing "$test_id" "$existing_metrics" "$APPLICATIONINSIGHTS_RESOURCE_ID" "$appi_type" "performanceCounters/processPrivateBytes" "microsoft.insights/components" "Average" && metrics_added=$((metrics_added + 1)) || true

        printf '    [OK] App Components present (%s added); server metrics present (%s added).\n' "$components_added" "$metrics_added"
    done
}

create_or_update_test() {
    file=$1
    test_id=$(read_yaml_value "$file" testId)
    test_type=$(read_yaml_value "$file" testType)
    engines=$(read_yaml_value "$file" engineInstances)
    [ -n "$test_id" ] || fail "No testId found in $(relative_path "$file")."
    [ -n "$test_type" ] || test_type=unknown
    [ -n "$engines" ] || engines=1

    rel=$(relative_path "$file")
    printf "  Creating %s as '%s'...\n" "$rel" "$test_id"
    set +e
    out=$(az load test create $(az_load_args "$test_id" "$file") 2>&1)
    code=$?
    set -e

    if [ "$code" -ne 0 ]; then
        if already_exists "$out" || [ -n "$(show_query "$test_id" 'testId')" ]; then
            printf '    Test already exists; updating it instead.\n'
            az load test update $(az_load_args "$test_id" "$file") >/dev/null || fail "Failed to update existing test '$test_id'."
        else
            fail "Failed to create test '$test_id'.\n$out"
        fi
    fi

    status=UNKNOWN
    attempt=1
    while [ "$attempt" -le 6 ]; do
        status=$(validation_status "$test_id")
        case "$status" in
            VALIDATION_INITIATED|VALIDATION_NOT_STARTED|UNKNOWN) sleep 2 ;;
            *) break ;;
        esac
        attempt=$((attempt + 1))
    done

    engines_from_service=$(show_query "$test_id" 'loadTestConfiguration.engineInstances')
    [ -n "$engines_from_service" ] && [ "$engines_from_service" != "None" ] && engines=$engines_from_service

    SUMMARY_ROWS="${SUMMARY_ROWS}${test_id}\t${test_type}\t${engines}\t${status}\n"
    [ "$status" = "VALIDATION_SUCCESS" ] || fail "Test '$test_id' reported validation status '$status'."
}

seed_demo_secret() {
    key_vault_name=$1
    [ -n "$key_vault_name" ] || {
        printf '  Key Vault not present in this environment; skipping demo secret seed.\n'
        return 0
    }

    set +e
    if [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
        show_out=$(az keyvault show --name "$key_vault_name" --subscription "$AZURE_SUBSCRIPTION_ID" --only-show-errors -o none 2>&1)
    else
        show_out=$(az keyvault show --name "$key_vault_name" --only-show-errors -o none 2>&1)
    fi
    show_code=$?
    set -e
    if [ "$show_code" -ne 0 ]; then
        printf '  [WARN] Key Vault output is set, but the vault was not found or is not visible yet. Skipping optional secret seed.\n%s\n' "$show_out" >&2
        return 0
    fi

    printf "  Seeding Key Vault secret 'demo-api-key' in '%s'...\n" "$key_vault_name"
    attempt=1
    while [ "$attempt" -le 5 ]; do
        set +e
        if [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
            out=$(az keyvault secret set --vault-name "$key_vault_name" --name demo-api-key --value contoso-demo-api-key --subscription "$AZURE_SUBSCRIPTION_ID" --only-show-errors -o none 2>&1)
        else
            out=$(az keyvault secret set --vault-name "$key_vault_name" --name demo-api-key --value contoso-demo-api-key --only-show-errors -o none 2>&1)
        fi
        code=$?
        set -e

        if [ "$code" -eq 0 ]; then
            printf '  [OK] Key Vault demo secret is present.\n'
            return 0
        fi
        if printf '%s' "$out" | grep -Eiq 'public network access is disabled|ForbiddenByConnection|not from a trusted service'; then
            printf "  [WARN] Key Vault '%s' has publicNetworkAccess=Disabled, so its data plane is unreachable from this machine.\n" "$key_vault_name" >&2
            printf "    This is an Azure Policy 'modify' effect in this subscription, not a misconfiguration in this repo:\n"
            printf "    the vault is created with public access enabled and the platform silently overrides it.\n"
            printf "    Consequence: 'contoso-bank-secrets-kv' will validate at create time but FAIL at run time.\n"
            printf "    That test is kept deliberately as the governance cautionary tale - see docs/11 and docs/20.\n"
            printf "    For a secret that actually resolves, use the CI pipeline-secret path in docs/08.\n"
            return 0
        fi
        if printf '%s' "$out" | grep -Eiq '403|forbidden|authorization'; then
            printf '  [WARN] Key Vault RBAC is not ready yet (attempt %s/5). Retrying shortly...\n' "$attempt" >&2
            sleep $((3 * attempt))
            attempt=$((attempt + 1))
            continue
        fi
        printf '  [WARN] Could not seed the optional Key Vault demo secret. The load-test definitions still continue.\n%s\n' "$out" >&2
        return 0
    done

    printf "  [WARN] Key Vault RBAC did not become ready in time. Re-run this hook later to seed 'demo-api-key'.\n" >&2
}

main() {
    cd "$REPO_ROOT"

    AZURE_RESOURCE_GROUP=$(get_hook_value AZURE_RESOURCE_GROUP true)
    AZURE_SUBSCRIPTION_ID=$(get_hook_value AZURE_SUBSCRIPTION_ID false)
    SERVICE_API_HOSTNAME=$(get_hook_value SERVICE_API_HOSTNAME true)
    LOAD_TEST_NAME=$(get_hook_value LOAD_TEST_NAME true)
    LOAD_TEST_RESOURCE_ID=$(get_hook_value LOAD_TEST_RESOURCE_ID false)
    KEY_VAULT_NAME=$(get_hook_value KEY_VAULT_NAME false)
    SERVICE_API_RESOURCE_ID=$(get_hook_value SERVICE_API_RESOURCE_ID true)
    APPLICATIONINSIGHTS_RESOURCE_ID=$(get_hook_value APPLICATIONINSIGHTS_RESOURCE_ID true)
    APP_SERVICE_PLAN_RESOURCE_ID=$(get_hook_value APP_SERVICE_PLAN_RESOURCE_ID true)

    if [ -z "$LOAD_TEST_RESOURCE_ID" ] && [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
        LOAD_TEST_RESOURCE_ID="/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$AZURE_RESOURCE_GROUP/providers/Microsoft.LoadTestService/loadTests/$LOAD_TEST_NAME"
    fi

    ensure_prerequisites
    retarget_artifacts "$SERVICE_API_HOSTNAME"
    retarget_keyvault "$KEY_VAULT_NAME"

    say_step "Seeding optional Key Vault demo secret"
    seed_demo_secret "$KEY_VAULT_NAME"

    say_step "Creating or updating Azure Load Testing definitions"
    mkdir -p "$REPO_ROOT/.azd-hooks"
    config_list="$REPO_ROOT/.azd-hooks/configs.$$"
    : > "$config_list"
    for file in "$REPO_ROOT"/loadtests/config/*.yaml; do
        [ -f "$file" ] && printf '%s\n' "$file" >> "$config_list"
    done
    sort "$config_list" -o "$config_list"
    found=0
    while IFS= read -r file; do
        found=1
        create_or_update_test "$file"
    done < "$config_list"
    rm -f "$config_list"

    if [ "$found" -eq 0 ]; then
        fail "No load test YAML configs found under loadtests/config."
    fi

    configure_server_monitoring

    say_step "Provisioned load test definitions"
    printf '%-36s %-10s %-7s %s\n' "Test id" "Test type" "Engines" "Validation status"
    printf '%-36s %-10s %-7s %s\n' "-------" "---------" "-------" "-----------------"
    printf '%b' "$SUMMARY_ROWS" | while IFS="$(printf '\t')" read -r id type engines status; do
        [ -n "$id" ] || continue
        printf '%-36s %-10s %-7s %s\n' "$id" "$type" "$engines" "$status"
    done

    if [ -n "$LOAD_TEST_RESOURCE_ID" ]; then
        printf 'Portal: https://portal.azure.com/#resource%s/tests\n' "$LOAD_TEST_RESOURCE_ID"
    fi
    printf 'No load tests were run. Start a run explicitly when you are ready to spend VUH.\n'
}

main "$@"
