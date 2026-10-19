# Strict local JSON interpretation only: no Azure operations or defaults to pass.
def finite: if type == "number" then (isnan | not) and (isinfinite | not) else false end;
def verdict($outcome; $reason): {outcome:$outcome,reason:$reason};
if .status != "DONE" then
  .status as $status | if (["FAILED","VALIDATION_FAILURE","CANCELLED","CANCELED","STOPPED"] | index($status)) then
    verdict("unjudgeable"; "Run did not complete")
  else verdict("pending"; "Waiting for DONE") end
else
  . as $run | .passFailCriteria.passFailMetrics as $metrics |
  if ($metrics | type) != "object" or ($metrics | length) == 0 then
    verdict("unjudgeable"; "No client criteria")
  elif .testResult != "PASSED" and .testResult != "FAILED" then
    verdict("pending"; "Aggregate verdict is not settled; NOT_APPLICABLE is not a pass")
  else
    ($metrics | [.[]]) as $criteria | .testRunStatistics.Total as $total |
    if ($criteria | all((.actualValue|finite) and (.value|finite) and
      (((.result // "")|ascii_downcase) == "passed" or ((.result // "")|ascii_downcase) == "failed"))) == false then
      verdict("pending"; "Numeric per-criterion verdicts not settled")
    elif ($total.sampleCount|finite) != true or $total.sampleCount <= 0 or
      ($total.pct2ResTime|finite) != true or ($total.errorPct|finite) != true then
      verdict("pending"; "Nonempty client statistics not settled")
    elif ($criteria|length) != 2 or
      ([$criteria[]|[(.aggregate|ascii_downcase),(.clientMetric|ascii_downcase),.condition,.value]]|sort) !=
      ([["p95","response_time_ms",">",500],["percentage","error",">",1]]|sort) or
      ($criteria|any((.requestName // "") != "")) then
      verdict("unjudgeable"; "Criteria differ from unchanged p95/error contract")
    elif $total.pct2ResTime < 0 or $total.errorPct < 0 or $total.errorPct > 100 or
      ($criteria|any(
        ((.actualValue > .value) != ((.result|ascii_downcase) == "failed")) or
        ((if .clientMetric == "error" then $total.errorPct else $total.pct2ResTime end) > .value) != (.actualValue > .value)
      )) then
      verdict("unjudgeable"; "Measurements contradict criterion verdicts")
    elif ($criteria|any((.result|ascii_downcase) == "failed")) then
      verdict("performance_failed"; "Measured criterion breached") +
      {samples:$total.sampleCount,p95_ms:$total.pct2ResTime,error_pct:$total.errorPct,criteria:$criteria}
    elif .testResult == "FAILED" then
      verdict("pending"; "Aggregate and individual verdicts have not converged")
    else
      verdict("passed"; "Aggregate and every measured criterion passed") +
      {samples:$total.sampleCount,p95_ms:$total.pct2ResTime,error_pct:$total.errorPct,criteria:$criteria}
    end
  end
end
