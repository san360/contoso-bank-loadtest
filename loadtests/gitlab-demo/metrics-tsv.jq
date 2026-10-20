# Console projection only; the input JSON remains the gate/artifact source.
def cell:
  if . == null then "N/A"
  elif type == "number" then tostring | sub("\\.0+$"; "")
  else . end;
def scope:
  (.resourceType | ascii_downcase) as $type |
  if $type == "microsoft.web/sites/slots" then "staging_slot"
  elif $type == "microsoft.web/serverfarms" then "shared_plan"
  else $type end;
def client_unit($metric):
  {ResponseTime:"ms",TotalRequests:"count",Errors:"count",VirtualUsers:"users"}[$metric] // "unknown";

(
  if $view == "requests" then
    ["request","samples","p95_ms","error_percent","throughput_rps"],
    ((.testRunStatistics // {}) | to_entries | sort_by(.key)[] |
      [(.value.transaction // .key),.value.sampleCount,.value.pct2ResTime,.value.errorPct,.value.throughput] | map(cell))
  elif $view == "client" then
    ["timestamp_utc","scope","metric","value","unit","status"],
    (to_entries | sort_by(.key)[] | .key as $metric |
      [.value[]?.data[]? | select(.value != null)] as $points |
      if ($points | length) == 0 then
        ["N/A","client",$metric,"N/A",client_unit($metric),"no samples"]
      else $points | sort_by(.timestamp)[] |
        [.timestamp,"client",$metric,.value,client_unit($metric),"measured"]
      end)
  elif $view == "server" then
    ($ARGS.named.scope | if . == "staging" then "staging_slot" else . end) as $scope |
    ["timestamp_utc","scope","metric","aggregation","value","unit","status"],
    ((.value // []) | sort_by(.name.value)[] | . as $metric |
      [.timeseries[]?.data[]? | . as $point |
        ["average","total","minimum","maximum","count"][] as $aggregation |
        select($point[$aggregation] != null) |
        [$point.timeStamp,$scope,$metric.name.value,$aggregation,$point[$aggregation],($metric.unit | cell),"measured"]
      ] as $points |
      if ($points | length) == 0 then
        ["N/A",$scope,$metric.name.value,"N/A","N/A",($metric.unit | cell),"no samples"]
      else $points | sort_by(.[0],.[3])[] end)
  elif $view == "definitions" then
    ["scope","name","namespace","aggregation","resource_id","unit"],
    (.metrics | to_entries | sort_by(.key)[] | .value |
      [scope,.name,.metricNamespace,.aggregation,.resourceId,(.unit | cell)])
  elif $view == "components" then
    ["scope","resource","type"],
    (.components | to_entries | sort_by(.key)[] | .value | [scope,.resourceName,.resourceType])
  else error("Unknown TSV view: " + $view) end
) | map(cell) | @tsv
