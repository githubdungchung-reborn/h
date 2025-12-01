#!/bin/bash
#
# 🔧 External Stress Test Tools
# =============================
# Alternative tools for stress testing when you need raw performance.
#
# Prerequisites:
#   - wrk:  sudo apt install wrk  OR  brew install wrk
#   - hey:  go install github.com/rakyll/hey@latest
#   - ab:   sudo apt install apache2-utils
#   - vegeta: go install github.com/tsenart/vegeta@latest
#
# Usage: ./external_tools.sh <tool> <url> [options]

set -e

URL="${2:-http://localhost:8000/api/products}"
DURATION="${3:-30s}"
CONCURRENCY="${4:-100}"
RPS="${5:-1000}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC} $1"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
}

case "$1" in
    # =========================================================================
    # WRK - High Performance HTTP Benchmark
    # =========================================================================
    wrk|wrk-basic)
        print_header "🚀 WRK Basic Test - ${CONCURRENCY} connections for ${DURATION}"
        wrk -t12 -c${CONCURRENCY} -d${DURATION} "${URL}"
        ;;

    wrk-latency)
        print_header "📊 WRK Latency Distribution"
        wrk -t12 -c${CONCURRENCY} -d${DURATION} --latency "${URL}"
        ;;

    wrk-extreme)
        print_header "💥 WRK EXTREME - 1000 connections"
        wrk -t12 -c1000 -d${DURATION} --latency "${URL}"
        ;;

    wrk-lua)
        # Create a Lua script for custom requests
        cat > /tmp/wrk_script.lua << 'EOF'
-- Custom WRK script with random delays and headers
request = function()
    headers = {}
    headers["Content-Type"] = "application/json"
    headers["X-Request-ID"] = string.format("%d-%d", os.time(), math.random(1000000))
    return wrk.format("GET", nil, headers)
end

response = function(status, headers, body)
    if status == 429 then
        rate_limited = (rate_limited or 0) + 1
    end
end

done = function(summary, latency, requests)
    io.write("------------------------------\n")
    io.write(string.format("Rate Limited (429): %d\n", rate_limited or 0))
end
EOF
        print_header "🎯 WRK with Custom Lua Script"
        wrk -t12 -c${CONCURRENCY} -d${DURATION} -s /tmp/wrk_script.lua --latency "${URL}"
        ;;

    # =========================================================================
    # HEY - HTTP Load Generator
    # =========================================================================
    hey|hey-basic)
        print_header "🎯 HEY Basic Test - ${RPS} RPS"
        hey -z ${DURATION} -c ${CONCURRENCY} -q ${RPS} "${URL}"
        ;;

    hey-burst)
        print_header "💥 HEY Burst - 10000 requests as fast as possible"
        hey -n 10000 -c ${CONCURRENCY} "${URL}"
        ;;

    hey-sustained)
        print_header "⏱️ HEY Sustained - 5000 RPS for ${DURATION}"
        hey -z ${DURATION} -c 500 -q 5000 "${URL}"
        ;;

    hey-max)
        print_header "🔥 HEY Maximum - No rate limit"
        hey -z ${DURATION} -c 1000 "${URL}"
        ;;

    # =========================================================================
    # AB - Apache Benchmark
    # =========================================================================
    ab|ab-basic)
        print_header "🔨 Apache Bench - 10000 requests"
        ab -n 10000 -c ${CONCURRENCY} "${URL}"
        ;;

    ab-keepalive)
        print_header "🔄 Apache Bench with Keep-Alive"
        ab -n 10000 -c ${CONCURRENCY} -k "${URL}"
        ;;

    # =========================================================================
    # VEGETA - HTTP Load Testing Tool
    # =========================================================================
    vegeta|vegeta-basic)
        print_header "🥬 Vegeta Attack - ${RPS} RPS for ${DURATION}"
        echo "GET ${URL}" | vegeta attack -rate=${RPS} -duration=${DURATION} | vegeta report
        ;;

    vegeta-plot)
        print_header "📈 Vegeta Attack with Plot"
        echo "GET ${URL}" | vegeta attack -rate=${RPS} -duration=${DURATION} | tee /tmp/vegeta_results.bin | vegeta report
        cat /tmp/vegeta_results.bin | vegeta plot > /tmp/vegeta_plot.html
        echo -e "${GREEN}Plot saved to: /tmp/vegeta_plot.html${NC}"
        ;;

    vegeta-ramp)
        print_header "📊 Vegeta Ramp-Up Attack"
        # Ramp from 100 to 5000 RPS over 60 seconds
        for rate in 100 500 1000 2000 3000 5000; do
            echo -e "${YELLOW}Testing at ${rate} RPS...${NC}"
            echo "GET ${URL}" | vegeta attack -rate=${rate} -duration=10s | vegeta report
            sleep 2
        done
        ;;

    # =========================================================================
    # CURL - Simple Tests
    # =========================================================================
    curl-burst)
        print_header "🌊 CURL Burst - 100 parallel requests"
        seq 100 | xargs -P100 -I{} curl -s -o /dev/null -w "%{http_code}\n" "${URL}" | sort | uniq -c
        ;;

    curl-loop)
        print_header "🔄 CURL Loop - Continuous requests"
        COUNT=0
        SUCCESS=0
        RATE_LIMITED=0
        START=$(date +%s)
        while true; do
            STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${URL}")
            ((COUNT++))
            if [ "$STATUS" == "200" ]; then
                ((SUCCESS++))
            elif [ "$STATUS" == "429" ]; then
                ((RATE_LIMITED++))
            fi
            NOW=$(date +%s)
            ELAPSED=$((NOW - START))
            if [ $ELAPSED -gt 0 ]; then
                RPS=$((COUNT / ELAPSED))
            else
                RPS=0
            fi
            echo -ne "\rRequests: $COUNT | Success: $SUCCESS | 429s: $RATE_LIMITED | RPS: $RPS    "
            if [ $ELAPSED -ge 30 ]; then
                echo ""
                break
            fi
        done
        ;;

    # =========================================================================
    # COMBINED SCENARIOS
    # =========================================================================
    all-tools)
        print_header "🎯 Running All Tools Comparison"
        echo -e "\n${YELLOW}1. WRK Test${NC}"
        wrk -t4 -c100 -d10s --latency "${URL}" 2>/dev/null || echo "wrk not installed"

        echo -e "\n${YELLOW}2. HEY Test${NC}"
        hey -z 10s -c 100 "${URL}" 2>/dev/null || echo "hey not installed"

        echo -e "\n${YELLOW}3. AB Test${NC}"
        ab -n 1000 -c 100 "${URL}" 2>/dev/null || echo "ab not installed"

        echo -e "\n${YELLOW}4. Vegeta Test${NC}"
        echo "GET ${URL}" | vegeta attack -rate=100 -duration=10s 2>/dev/null | vegeta report || echo "vegeta not installed"
        ;;

    progressive)
        print_header "📈 Progressive Load Test"
        for c in 10 50 100 200 500 1000; do
            echo -e "\n${YELLOW}Testing with $c connections...${NC}"
            wrk -t4 -c$c -d10s "${URL}" 2>/dev/null || hey -z 10s -c $c "${URL}" 2>/dev/null
            sleep 2
        done
        ;;

    find-limit)
        print_header "🔍 Finding Rate Limit"
        for rps in 100 500 1000 2000 5000 10000; do
            echo -e "\n${YELLOW}Testing at $rps RPS...${NC}"
            RESULT=$(hey -z 5s -c 100 -q $rps "${URL}" 2>/dev/null | grep "429" || echo "0")
            echo "429 responses: $RESULT"
            if echo "$RESULT" | grep -q "[1-9]"; then
                echo -e "${RED}Rate limit detected around $rps RPS${NC}"
                break
            fi
        done
        ;;

    # =========================================================================
    # HELP
    # =========================================================================
    help|*)
        echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}║     🔧 External Stress Test Tools                              ║${NC}"
        echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo "Usage: $0 <command> <url> [duration] [concurrency] [rps]"
        echo ""
        echo -e "${YELLOW}WRK Commands:${NC}"
        echo "  wrk           Basic WRK test"
        echo "  wrk-latency   WRK with latency distribution"
        echo "  wrk-extreme   WRK with 1000 connections"
        echo "  wrk-lua       WRK with custom Lua script"
        echo ""
        echo -e "${YELLOW}HEY Commands:${NC}"
        echo "  hey           Basic HEY test"
        echo "  hey-burst     10000 requests burst"
        echo "  hey-sustained Sustained 5000 RPS"
        echo "  hey-max       Maximum throughput"
        echo ""
        echo -e "${YELLOW}Apache Bench:${NC}"
        echo "  ab            Basic AB test"
        echo "  ab-keepalive  AB with keep-alive"
        echo ""
        echo -e "${YELLOW}Vegeta:${NC}"
        echo "  vegeta        Basic vegeta attack"
        echo "  vegeta-plot   Vegeta with HTML plot"
        echo "  vegeta-ramp   Ramp-up attack"
        echo ""
        echo -e "${YELLOW}CURL:${NC}"
        echo "  curl-burst    100 parallel curl requests"
        echo "  curl-loop     Continuous curl loop"
        echo ""
        echo -e "${YELLOW}Combined:${NC}"
        echo "  all-tools     Run all tools comparison"
        echo "  progressive   Progressive load increase"
        echo "  find-limit    Find rate limit threshold"
        echo ""
        echo -e "${BLUE}Examples:${NC}"
        echo "  $0 wrk http://localhost:8000/api/products 30s 100"
        echo "  $0 hey-burst http://localhost:8000/api/products"
        echo "  $0 vegeta-ramp http://localhost:8000/api/products"
        ;;
esac
