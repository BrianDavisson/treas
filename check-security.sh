#!/bin/bash
# Security check script for Treasury Analyzer

echo "��� Security Check for Treasury Analyzer"
echo "======================================="

# Check for sensitive files
echo "1. Checking for sensitive files..."
sensitive_files=$(find . -name "*.env" -o -name "*.key" -o -name "*.json" -o -name "*.tfvars" -o -name "*.tfstate*" | grep -v ".vscode/tasks.json")
if [ -z "$sensitive_files" ]; then
    echo "✅ No sensitive files found"
else
    echo "⚠️  Sensitive files found:"
    echo "$sensitive_files"
fi

# Check for sensitive content in staged files
echo ""
echo "2. Checking staged files for sensitive content..."
if git diff --cached | grep -iE "(password|secret|api_key|private_key|service_account)" > /dev/null; then
    echo "⚠️  Potential sensitive content in staged files"
    git diff --cached | grep -iE "(password|secret|api_key|private_key|service_account)" | head -3
else
    echo "✅ No sensitive content in staged files"
fi

# Check .gitignore
echo ""
echo "3. Checking .gitignore coverage..."
required_patterns=("*.env" "*.key" "*.tfvars" "*.tfstate" "*-key.json")
missing_patterns=()
for pattern in "${required_patterns[@]}"; do
    if ! grep -q "$pattern" .gitignore; then
        missing_patterns+=("$pattern")
    fi
done

if [ ${#missing_patterns[@]} -eq 0 ]; then
    echo "✅ .gitignore has all required security patterns"
else
    echo "⚠️  Missing .gitignore patterns: ${missing_patterns[*]}"
fi

echo ""
echo "4. Summary:"
if [ -z "$sensitive_files" ] && [ ${#missing_patterns[@]} -eq 0 ]; then
    echo "✅ SAFE TO PUSH - No security issues detected"
else
    echo "⚠️  REVIEW REQUIRED - Address issues above before pushing"
fi
