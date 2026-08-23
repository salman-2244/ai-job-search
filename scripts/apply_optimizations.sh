#!/bin/bash
# Apply CV and Cover Letter optimizations safely

PROJECT_DIR="/Users/salman/Projects/ai-job-search"
CV_DIR="$PROJECT_DIR/cv"
COVER_DIR="$PROJECT_DIR/cover_letters"

# Function to remove AWARDS section from a CV
remove_awards() {
    local cv_file="$1"
    local tmp_file="${cv_file}.tmp"

    # Remove the entire AWARDS section (from %---------- AWARDS to next %---------- or end)
    # Handle both AWARDS and AWARDS & CERTIFICATIONS
    awk '
    BEGIN { in_awards=0; skip=0 }
    /%----------.*AWARDS/ { in_awards=1; skip=1; next }
    in_awards && /%----------/ { in_awards=0; skip=0; print; next }
    in_awards { skip=1 }
    !skip { print }
    ' "$cv_file" > "$tmp_file" 2>/dev/null && mv "$tmp_file" "$cv_file"
}

# Function to fix cover letter closing
fix_cover_letter() {
    local cl_file="$1"
    # Remove \[10pt] from the file
    sed -i '' 's/\\\[10pt\\]//g' "$cl_file"
}

# Function to enhance summary (simple replacement for now)
enhance_summary() {
    local cv_file="$1"
    local tmp_file="${cv_file}.tmp"

    # This is a placeholder - we need a more sophisticated approach
    # For now, just ensure the summary exists
    grep -q "SUMMARY" "$cv_file" || return 1

    return 0
}

echo "=== Applying Optimizations ==="
echo ""

# Process all CV folders
echo "Processing CVs..."
for cv_dir in "$CV_DIR"/*/; do
    [ -d "$cv_dir" ] || continue
    [ -f "$cv_dir/main.tex" ] || continue

    folder=$(basename "$cv_dir")
    echo "  $folder..."

    # Remove awards
    if grep -qi "AWARDS\|Certifications" "$cv_dir/main.tex" 2>/dev/null; then
        remove_awards "$cv_dir/main.tex"
        echo "    - Removed AWARDS section"
    fi

    # Enhance summary (placeholder)
    # enhance_summary "$cv_dir/main.tex"

    # Recompile PDF
    if [ -f "$cv_dir/main.tex" ]; then
        (cd "$cv_dir" && lualatex -interaction=nonstopmode main.tex > /dev/null 2>&1 && echo "    - Recompiled PDF") || echo "    - Compilation failed"
    fi
done

echo ""
echo "Processing Cover Letters..."
for cl_dir in "$COVER_DIR"/*/; do
    [ -d "$cl_dir" ] || continue
    [ -f "$cl_dir/cover.tex" ] || continue

    folder=$(basename "$cl_dir")
    echo "  $folder..."

    # Fix closing spacing
    if grep -q '\\\[10pt\\]' "$cl_dir/cover.tex" 2>/dev/null; then
        fix_cover_letter "$cl_dir/cover.tex"
        echo "    - Fixed closing spacing"
    fi

    # Recompile PDF
    if [ -f "$cl_dir/cover.tex" ]; then
        (cd "$cl_dir" && lualatex -interaction=nonstopmode cover.tex > /dev/null 2>&1 && echo "    - Recompiled PDF") || echo "    - Compilation failed"
    fi
done

echo ""
echo "=== Optimizations Complete ==="
