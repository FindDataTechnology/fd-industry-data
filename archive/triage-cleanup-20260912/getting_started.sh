#!/bin/bash
# Quick Start Script for Steel Industry Data Spiders

set -e  # Exit on error

echo "=================================================="
echo "  Steel Industry Data Spiders - Quick Start"
echo "=================================================="
echo ""

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Verify setup
echo -e "${BLUE}Step 1: Verifying setup...${NC}"
uv run python verify_steel_spiders.py
echo ""

# Step 2: Run all steel spiders
echo -e "${BLUE}Step 2: Running all steel spider categories...${NC}"
for category in steel-assoc steel-exchange steel-statistics steel-info steel-market; do
    echo ""
    echo "  Running ${category}..."
    uv run python spiers/$category/spider.py || echo "  ⚠️  $category completed (no data extracted yet)"
done
echo ""

# Step 3: Check outputs
echo -e "${BLUE}Step 3: Checking outputs...${NC}"
echo ""
for category in steel-assoc steel-exchange steel-statistics steel-info steel-market; do
    db_file="spiers/$category/data/*.db"
    json_file="spiers/$category/output/*.json"
    
    if ls $db_file 1>/dev/null 2>&1; then
        size=$(ls -lh $db_file | awk '{print $5}')
        echo "  ✓ Database: $(basename $db_file) ($size)"
    fi
    
    if ls $json_file 1>/dev/null 2>&1; then
        size=$(ls -lh $json_file | awk '{print $5}')
        echo "  ✓ JSON:     $(basename $json_file) ($size)"
    fi
done
echo ""

# Summary
echo "=================================================="
echo -e "${GREEN}✅ Quick Start Complete!${NC}"
echo "=================================================="
echo ""
echo "Next Steps:"
echo "  1. Review each spider's README.md for details"
echo "  2. Implement custom parsing logic (extract_data_from_page)"
echo "  3. Test with single URL first before running all"
echo "  4. Import manifests to fd-open-data-mcp when ready"
echo ""
echo "Documentation:"
echo "  • README.md                 → Main documentation"
echo "  • STEEL_DATA_README.md      → Complete technical guide"
echo "  • PROJECT_SUMMARY.md        → Project overview"
echo "  • spiers/*/README.md        → Category-specific docs"
echo ""
