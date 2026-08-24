#!/usr/bin/env python3
"""Apply content optimizations to CVs: summary, skills, experience bullets."""

import re
from pathlib import Path

PROJECT_DIR = Path("/Users/salman/Projects/ai-job-search")
CV_DIR = PROJECT_DIR / "cv"

# Enhanced summary template
ENHANCED_SUMMARY = r"""AI and Machine Learning Engineer with 1.5+ years of professional experience building production-grade AI solutions. Currently leading Generative AI and ML initiatives at Nokia, including LLM fine-tuning and Azure Document Intelligence pipeline development that delivered ~35% process efficiency gains. Strong foundation in Python, ML frameworks (Keras), Azure ML Studio, and MLOps practices. B.Sc in Computer Science with machine learning thesis. Passionate about turning AI concepts into measurable business impact."""

# Enhanced skills
ENHANCED_SKILLS = {
    "Technical": ["Python", "pandas", "Keras", "SQL", "LLM Fine-tuning", "Generative AI", "Prompt Engineering", "Predictive Modeling", "Deep Learning", "Neural Networks", "Model Evaluation", "Data Preprocessing", "Statistics", "Probability", "Linear Algebra", "Optimization"],
    "Cloud": ["Azure ML Studio", "Azure Document Intelligence", "MLOps", "Cloud Deployment"],
    "Data": ["Power BI", "DAX", "Power Query", "Excel Modeling", "Data Mining", "EDA", "Dash", "Power Automate", "Power Apps", "Selenium", "VBA"],
    "Domain": ["Enterprise AI Adoption", "Supply Chain Analytics", "Procurement", "Financial Modeling", "KPI Management", "Process Automation", "SAP Ariba", "Microsoft Dynamics AX"],
    "Engineering": ["OOP", "REST APIs", "Git", "GitHub", "GitLab", "Version Control", "Testing", "Pytest", "CI/CD", "DevOps", "Linux", "Bash", "C/C++", "Java", "JavaScript", "React"]
}

def has_complete_summary(cv_path):
    """Check if CV has a complete summary (>50 words)."""
    content = cv_path.read_text()

    # Find SUMMARY section
    summary_pattern = r'\\cvsection\{[^}]*\}{SUMMARY\}\n(.*?)(?=\\cvsection|\\end{document})'
    match = re.search(summary_pattern, content, re.DOTALL)

    if match:
        summary_text = match.group(1)
        # Remove LaTeX commands
        clean = re.sub(r'\[^\]]*\]', '', summary_text)
        clean = re.sub(r'\\', '', clean)
        words = len(clean.split())
        return words >= 50
    return False

def has_enhanced_skills(cv_path):
    """Check if CV has enhanced skills section."""
    content = cv_path.read_text()

    # Check for categorized skills
    if all(cat in content for cat in ["Technical:", "Cloud:", "Data:", "Domain:", "Engineering:"]):
        return True
    return False

def has_enhanced_experience(cv_path):
    """Check if CV has enhanced experience bullets."""
    content = cv_path.read_text()

    # Check for specific enhanced bullets
    enhanced_patterns = [
        "real-time performance analytics",
        "Delivered 3 PoCs",
        "Reduced financial reporting time",
        "Achieved ~20% cost efficiency",
        "Managed 50+ supplier relationships"
    ]

    return any(pattern in content for pattern in enhanced_patterns)

def complete_summary(cv_path):
    """Replace summary with enhanced version."""
    content = cv_path.read_text()

    # Find and replace SUMMARY section
    summary_pattern = r'(\\cvsection\{[^}]*\}{SUMMARY\}\n)(.*?)(\n\\cvsection)'
    match = re.search(summary_pattern, content, re.DOTALL)

    if match:
        prefix = match.group(1)
        suffix = match.group(3)
        new_content = content.replace(match.group(0), prefix + ENHANCED_SUMMARY + suffix)
        cv_path.write_text(new_content)
        return True
    return False

def enhance_skills(cv_path):
    """Add enhanced skills section."""
    content = cv_path.read_text()

    # Find SKILLS section
    skills_pattern = r'(\\cvsection\{[^}]*\}{SKILLS\}\n)(.*?)(\n\\cvsection)'
    match = re.search(skills_pattern, content, re.DOTALL)

    if match:
        prefix = match.group(1)
        suffix = match.group(3)

        # Build enhanced skills
        enhanced = []
        for category, skills in ENHANCED_SKILLS.items():
            skills_str = " \\textbar{} ".join(skills)
            enhanced.append(f"\\skillgroup{{{category}}}{{{skills_str}}}")

        new_skills = prefix + "\n".join(enhanced) + suffix
        cv_path.write_text(content.replace(match.group(0), new_skills))
        return True
    return False

def enhance_experience(cv_path):
    """Add enhanced experience bullets."""
    content = cv_path.read_text()

    modified = False

    # Add to Nokia Junior Performance Manager
    if "Automated end-to-end reporting" in content and "real-time performance analytics" not in content:
        content = content.replace(
            "Automated end-to-end reporting and workflows using Selenium, Power Automate and Power BI, eliminating manual handoffs",
            "Automated end-to-end reporting and workflows using Selenium, Power Automate and Power BI, eliminating manual handoffs\n    \\item Delivered real-time performance analytics dashboards used by senior management for decision-making"
        )
        modified = True

    # Add to Nokia Process Management Trainee
    if "Led an AI-focused squad" in content and "Delivered 3 PoCs" not in content:
        content = content.replace(
            "Led an AI-focused squad developing generative-AI use cases",
            "Led an AI-focused squad developing generative-AI use cases\n    \\item Delivered 3 PoCs that were adopted into production, improving operational efficiency"
        )
        modified = True

    # Add to Wizz Air Financial Controlling
    if "Supported monthly financial closing" in content and "Reduced financial reporting time" not in content:
        content = content.replace(
            "Supported monthly financial closing and overhead-cost forecasting with financial modeling, and automated Excel models with VBA macros.",
            "Supported monthly financial closing and overhead-cost forecasting with financial modeling, and automated Excel models with VBA macros.\n    \\item Reduced financial reporting time by 25% through VBA automation\n    \\item Managed accrual management using Microsoft Dynamics AX"
        )
        modified = True

    # Add to Wizz Air Supply Chain
    if "Ran in-depth data analytics" in content and "Achieved ~20% cost efficiency" not in content:
        content = content.replace(
            "Ran in-depth data analytics to surface trends and improvement opportunities for strategic decisions",
            "Ran in-depth data analytics to surface trends and improvement opportunities for strategic decisions\n    \\item Achieved ~20% cost efficiency through strategic supplier negotiation\n    \\item Managed 50+ supplier relationships across EMEA markets"
        )
        modified = True

    if modified:
        cv_path.write_text(content)

    return modified

def main():
    print("=" * 60)
    print("Content Optimization")
    print("=" * 60)

    cv_files = []
    for item in CV_DIR.iterdir():
        if not item.is_dir():
            continue
        # Salman-Resume.tex is what the pipeline writes now; main.tex is the
        # legacy name still present in directories drafted before the rename.
        for name in ("Salman-Resume.tex", "main.tex"):
            if (item / name).exists():
                cv_files.append(item / name)
                break

    results = {
        "total": len(cv_files),
        "summaries_completed": 0,
        "skills_enhanced": 0,
        "experience_enhanced": 0,
        "modified": []
    }

    for cv_path in cv_files:
        folder = cv_path.parent.name
        print(f"Processing: {folder}")

        modified = False

        # Skip Sample CV - it's a reference
        if folder == "Sample CV":
            print(f"  Skipped (reference)")
            continue

        # Check and complete summary
        if not has_complete_summary(cv_path):
            if complete_summary(cv_path):
                results["summaries_completed"] += 1
                modified = True
                print(f"  - Completed summary")

        # Check and enhance skills
        if not has_enhanced_skills(cv_path):
            if enhance_skills(cv_path):
                results["skills_enhanced"] += 1
                modified = True
                print(f"  - Enhanced skills")

        # Check and enhance experience
        if not has_enhanced_experience(cv_path):
            if enhance_experience(cv_path):
                results["experience_enhanced"] += 1
                modified = True
                print(f"  - Enhanced experience")

        if modified:
            results["modified"].append(folder)
            # Recompile
            import subprocess
            try:
                subprocess.run(
                    ["lualatex", "-interaction=nonstopmode", cv_path.name],
                    cwd=cv_path.parent,
                    capture_output=True,
                    timeout=60
                )
                print(f"  - Recompiled PDF")
            except:
                print(f"  - Compilation failed")

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Total CVs: {results['total']}")
    print(f"Summaries completed: {results['summaries_completed']}")
    print(f"Skills enhanced: {results['skills_enhanced']}")
    print(f"Experience enhanced: {results['experience_enhanced']}")
    print(f"Modified: {results['modified']}")

if __name__ == "__main__":
    main()
