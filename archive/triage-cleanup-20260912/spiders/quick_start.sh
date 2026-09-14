#!/bin/bash
# Quick Start Guide - Industry Association & Commodity Exchange Scrapers
# This script provides a quick reference for running all 5 spiders

echo "======================================================================"
echo "Industry Association & Commodity Exchange Scrapers - Quick Start"
echo "======================================================================"
echo ""

# Check if we're in the right directory
if [ ! -d "spiers" ]; then
    echo "Error: Please run this script from /Users/chengsishi/finddata/fd-industry-data/"
    exit 1
fi

echo "Available Spiders:"
echo "  1. CISA Spider      - Steel production statistics (Score: 98)"
echo "  2. AMAC Spider      - Fund registration & AUM data (Score: 98)"
echo "  3. SAC Spider       - Securities trading volume (Score: 95)"
echo "  4. CME Ag Spider    - Agricultural futures prices (Score: 90)"
echo "  5. SHFE Spider      - Metal futures settlement (Score: 92)"
echo ""

echo "======================================================================"
echo "Running Individual Spiders"
echo "======================================================================"
echo ""

# Function to run a spider
run_spider() {
    local spider_name=$1
    local spider_file=$2
    local description=$3
    
    echo "----------------------------------------------------------------------"
    echo "Running: $spider_name"
    echo "Description: $description"
    echo "----------------------------------------------------------------------"
    
    if [ -f "spiers/$spider_file" ]; then
        uv run python "spiers/$spider_file"
        
        if [ $? -eq 0 ]; then
            echo "✓ $spider_name completed successfully"
        else
            echo "✗ $spider_name failed"
        fi
    else
        echo "✗ Spider file not found: spiers/$spider_file"
    fi
    echo ""
}

# Menu
echo "Select an option:"
echo "  1) Run CISA Spider (Steel Production)"
echo "  2) Run AMAC Spider (Fund Statistics)"
echo "  3) Run SAC Spider (Securities Trading)"
echo "  4) Run CME Agriculture Spider (Ag Futures)"
echo "  5) Run SHFE Spider (Metal Futures)"
echo "  6) Run ALL spiders"
echo "  7) View output files"
echo "  8) Exit"
echo ""

read -p "Enter choice [1-8]: " choice

case $choice in
    1)
        run_spider "CISA" "cisa_spider.py" "China Iron & Steel Association - Production Statistics"
        ;;
    2)
        run_spider "AMAC" "mac_spider.py" "Asset Management Association - Fund Registration"
        ;;
    3)
        run_spider "SAC" "sac_spider.py" "Securities Association - Trading Volume"
        ;;
    4)
        run_spider "CME Agriculture" "cmegroup_ag_spider.py" "CME Group - Agricultural Futures"
        ;;
    5)
        run_spider "SHFE" "shfe_spider.py" "Shanghai Futures Exchange - Metal Futures"
        ;;
    6)
        echo "Running all spiders..."
        echo ""
        run_spider "CISA" "cisa_spider.py" "China Iron & Steel Association"
        run_spider "AMAC" "mac_spider.py" "Asset Management Association"
        run_spider "SAC" "sac_spider.py" "Securities Association"
        run_spider "CME Agriculture" "cmegroup_ag_spider.py" "CME Group Agriculture"
        run_spider "SHFE" "shfe_spider.py" "Shanghai Futures Exchange"
        echo "All spiders completed!"
        ;;
    7)
        echo ""
        echo "======================================================================"
        echo "Output Files"
        echo "======================================================================"
        echo ""
        
        for spider in cisa_spider mac_spider sac_spider cmegroup_ag_spider shfe_spider; do
            echo "Spider: $spider"
            echo "  Database: spiers/$spider/data/*.db"
            echo "  JSON:     spiers/$spider/output/*.json"
            
            if [ -d "spiers/$spider/data" ]; then
                echo "  Status:   ✓ Directory exists"
                ls -lh "spiers/$spider/data/" 2>/dev/null | grep -E "\.db$" || echo "            (no database files yet)"
            else
                echo "  Status:   ✗ Not run yet"
            fi
            echo ""
        done
        ;;
    8)
        echo "Exiting..."
        exit 0
        ;;
    *)
        echo "Invalid choice. Please run again and select 1-8."
        exit 1
        ;;
esac

echo ""
echo "======================================================================"
echo "Quick Reference Commands"
echo "======================================================================"
echo ""
echo "View JSON output:"
echo "  cat spiers/cisa_spider/output/production_statistics.json | python -m json.tool"
echo ""
echo "Query SQLite database:"
echo "  sqlite3 spiers/cisa_spider/data/cisa_production.db"
echo "  SELECT * FROM production_stats LIMIT 10;"
echo ""
echo "Check file sizes:"
echo "  ls -lh spiers/*/data/*.db spiers/*/output/*.json"
echo ""
echo "For detailed documentation, see:"
echo "  spiders/README.md"
echo ""
echo "======================================================================"
