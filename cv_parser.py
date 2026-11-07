"""Extract CV evidence without silently augmenting it with the default profile."""
from pathlib import Path
import hashlib
import re
import pymupdf as fitz
from models import normalized

SKILLS = {
    "Python": ["python"], "Bash": ["bash"], "PHP": ["php"], "Laravel": ["laravel"],
    "Java": ["java"], "JavaScript": ["javascript", "js"], "TypeScript": ["typescript"],
    "React": ["react"], "Go": ["golang"], "Rust": ["rust"], "SQL": ["sql"],
    "GitLab": ["gitlab"], "CI/CD": ["ci/cd", "ci cd", "cicd"], "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k3s", "k8s"], "ArgoCD": ["argocd", "argo cd"],
    "Semgrep": ["semgrep"], "Trivy": ["trivy"], "Checkov": ["checkov"], "Vault": ["vault"],
    "Kyverno": ["kyverno"], "Cosign": ["cosign"], "Splunk": ["splunk"],
    "Microsoft Sentinel": ["sentinel", "microsoft sentinel"], "Wazuh": ["wazuh"],
    "Falco": ["falco"], "Active Directory": ["active directory"], "Entra ID": ["entra id"],
    "Azure": ["azure"], "AWS": ["aws", "amazon web services"], "GCP": ["gcp", "google cloud"],
    "Terraform": ["terraform"], "Ansible": ["ansible"], "Jenkins": ["jenkins"],
    "Burp Suite": ["burp", "burp suite"], "BloodHound": ["bloodhound"],
    "Suricata": ["suricata"], "pfSense": ["pfsense"], "Linux": ["linux"],
    "Windows Server": ["windows server"], "OpenStack": ["openstack"], "Oracle Cloud": ["oracle cloud", "oci"],
    "Nmap": ["nmap"], "Metasploit": ["metasploit"], "Mimikatz": ["mimikatz"],
    "MITRE ATT&CK": ["mitre att&ck", "mitre attack"], "OWASP": ["owasp"],
    "OpenSearch": ["opensearch"], "Wireshark": ["wireshark"], "Snort": ["snort"],
    "OpenVPN": ["openvpn"], "Git": ["git"], "Jira": ["jira"], "SOAR": ["soar"],
}
DOMAINS = {
    "DevSecOps": ["devsecops", "sast", "securite des conteneurs"],
    "DevOps": ["devops", "ci/cd", "kubernetes", "gitlab", "argocd"],
    "Cloud": ["cloud", "azure", "aws", "entra"],
    "SOC": ["soc", "siem", "splunk", "sentinel", "wazuh", "detection", "incident response", "reponse aux incidents"],
    "Purple Team": ["purple team", "red team", "blue team"],
    "Pentest": ["pentest", "penetration", "burp", "offensive", "owasp"],
    "Software development": ["software", "developpeur", "developer", "developpement", "laravel", "backend", "frontend", "full stack"],
    "Cybersecurity": ["cybersecurity", "cybersecurite", "security", "securite", "ssi"],
}

def contains(text, phrase):
    return bool(re.search(r"(?<!\w)" + re.escape(normalized(phrase)) + r"(?!\w)", normalized(text)))

def extract_skills(text):
    return sorted(k for k, aliases in SKILLS.items() if any(contains(text, a) for a in aliases))

def extract_domains(text):
    return sorted(k for k, aliases in DOMAINS.items() if any(contains(text, a) for a in aliases))

def parse_cv(path):
    path = Path(path)
    if not path.is_file(): raise ValueError("No CV PDF found. Upload your CV in the sidebar before running a session.")
    if path.stat().st_size > 10 * 1024 * 1024: raise ValueError("CV PDF is larger than 10 MB.")
    try:
        with fitz.open(path) as pdf:
            if pdf.needs_pass: raise ValueError("The CV is password-protected. Upload an unlocked PDF.")
            # PDF content order preserves multi-column CV reading order better than
            # geometric sorting, which can splice skills into experience paragraphs.
            text = "\n".join(page.get_text(sort=False) for page in pdf)
    except (RuntimeError, fitz.FileDataError) as exc:
        raise ValueError("Could not read the CV as a PDF.") from exc
    # Join typography hyphenation, including names split across lines.
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text).strip()
    if len(text) < 100: raise ValueError("This CV has no usable text layer. Export a searchable PDF or run OCR first.")
    # Only the experience section supplies professional experience evidence.
    start = re.search(r"exp[eé]rience[s]?\s+professionnelle[s]?|professional experience|work experience", text, re.I)
    experience = ""
    if start:
        experience = text[start.end():]
        end = re.search(r"projets?\s+pratiques?|education|formation|personal projects", experience, re.I)
        if end: experience = experience[:end.start()]
    return {"text": text, "skills": extract_skills(text), "domains": extract_domains(text),
            "experience_text": experience, "experience_domains": extract_domains(experience),
            "path": str(path), "filename": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "preferences": {"start": "2027-02", "duration_months": 6,
                            "cities": ["Casablanca", "Rabat", "Marrakech"], "remote": True}}
