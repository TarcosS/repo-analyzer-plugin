#!/usr/bin/env python3
"""
render_report.py — turns facts.json + agent JSON outputs into HTML pages, a Word report and a PDF report,
one folder per language.

  <out>/facts.json                    (collect_facts.py, shared by all languages)
  <out>/data/<lang>/overview.json     (orchestrator)
  <out>/data/<lang>/architecture.json (repo-architect)
  <out>/data/<lang>/features.json     (feature-mapper)
  <out>/data/<lang>/flows*.json       (flow-tracer, one or many)
  <out>/data/<lang>/health.json       (quality-auditor)
  (legacy single-language layout <out>/data/*.json is still read)

Output:
  <out>/index.html                    language chooser
  <out>/<lang>/*.html                 6 HTML pages
  <out>/<lang>/<repo>-analysis-<lang>.docx / .pdf   (export_docs.py; needs python-docx, playwright, pypdf)

Usage:
  python render_report.py --out DIR                              every language found under DIR/data, html+docx+pdf
  python render_report.py --out DIR --langs en,tr --formats html,pdf
  python render_report.py --out DIR --check                      validate JSON, file refs, mermaid and translation
                                                                 parity against the source language; exit 1 on issues
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import posixpath
import random
import re
import sys
from pathlib import Path

esc = lambda s: html.escape(str(s if s is not None else ""), quote=True)  # noqa: E731

# --------------------------------------------------------------------------- i18n

L = {
    "tr": {
        "pages": {
            "index": ("Genel bakış", "Repo ne yapıyor, neyle yazılmış, ne kadar canlı."),
            "architecture": ("Mimari", "Sistem sınırları, çalışan parçalar ve modüller arası bağımlılıklar."),
            "features": ("Özellikler", "Sunulan yetenekler ve her birinin koddaki giriş noktaları."),
            "flows": ("Akışlar", "Seçili özelliklerin adım adım nasıl çalıştığı."),
            "health": ("Sağlık ve riskler", "Sıcak noktalar, bilgi adaları, test durumu ve bulgular."),
            "onboarding": ("Başlangıç rehberi", "Çalıştırma, okuma sırası, sözlük ve başlangıç görevleri."),
            "conformance": ("Doküman uyumu", "Dış dokümandaki iddiaların kod karşısında doğrulanması."),
            "feasibility": ("Yapılabilirlik", "Bizden beklenenin gerçekte yapılabilir olup olmadığı."),
            "readiness": ("MVP hazırlığı", "MVP kapsamı, verilmesi gereken mimari kararlar ve iş kalemleri."),
        },
        "derived": "Koddan türetildi", "model": "Model yorumu", "expand": "Büyüt", "close": "Kapat",
        "copy": "Mermaid kaynağını kopyala", "copied": "Kopyalandı", "legend_solid": "Düz çerçeve: koddan deterministik olarak türetildi.",
        "legend_dashed": "Kesikli çerçeve: modelin kodu okuyarak yaptığı yorum. Referansları kontrol et.",
        "missing_ref": "Bu yol repoda bulunamadı", "missing_refs": "repoda bulunamayan referans",
        "empty": "Bu bölüm üretilmedi: {file} bulunamadı. Agent'ı tekrar çalıştır ya da ilgili subagent'ı tek başına çağır.",
        "files": "dosya", "lines": "satır", "languages": "Dil dağılımı", "stack": "Teknoloji yığını",
        "activity": "Commit aktivitesi (son 24 ay)", "purpose": "Amaç", "audience": "Kimin için",
        "commits": "commit", "contributors": "katkıcı", "tests": "test/kaynak satır oranı",
        "context": "Bağlam diyagramı", "containers": "Çalışan parçalar (container)", "components": "Bileşenler",
        "layers": "Katmanlar", "external": "Dış sistemler", "datamodel": "Veri modeli", "decisions": "Temel kararlar",
        "depgraph": "Dizinler arası import grafiği", "infra": "Altyapı ve operasyon", "style": "Mimari stil",
        "featuremap": "Özellik haritası", "entry": "Giriş noktaları", "keyfiles": "Ana dosyalar",
        "dependson": "Bağlı olduğu özellikler", "flowlinks": "Akışlar", "confidence": "güven", "routes": "Tespit edilen route'lar",
        "trigger": "Tetikleyen", "steps": "Adımlar", "errors": "Hata yolları", "sidefx": "Yan etkiler", "notes": "Notlar",
        "findings": "Bulgular", "strengths": "Güçlü yanlar", "hotspots": "Sıcak noktalar", "coupling": "Birlikte değişen dosyalar",
        "busfactor": "Bus factor (bilgi yoğunlaşması)", "testing": "Test durumu", "markers": "TODO / FIXME işaretleri",
        "secrets": "Olası gizli bilgi (değerler kaydedilmedi)", "largest": "En büyük dosyalar",
        "howtorun": "Çalıştırma", "reading": "Okuma sırası", "glossary": "Sözlük", "tasks": "Başlangıç ve değerlendirme görevleri",
        "tasks_note": "Gerçek repo üzerinde tanımlı görevler: onboarding için ya da repo tabanlı teknik değerlendirme için kullanılabilir.",
        "acceptance": "Kabul kriterleri", "envkeys": "Ortam değişkenleri (yalnızca isimler)",
        "score": "skor", "fixes": "fix commit", "authors": "yazar", "shared": "ortak commit", "degree": "bağlılık",
        "dir": "dizin", "bf": "bus factor", "topauthor": "en aktif yazar", "share": "pay",
        "recommendation": "Öneri", "gaps": "Eksik kalan alanlar", "frameworks": "Test araçları", "why": "Neden",
        "generated": "Üretildi", "nogit": "Git geçmişi yok; aktivite, sıcak nokta ve bus factor hesaplanmadı.",
        "noflows": "Akış verisi yok.", "flow_for": "Özellik",
        "render_err": "Diyagram çizilemedi. Mermaid sözdizimi hatası:", "offline": "Mermaid yüklenemedi (çevrimdışı olabilirsin). Kaynak aşağıda.",
    },
    "en": {
        "pages": {
            "index": ("Overview", "What the repo does, what it's built with, how alive it is."),
            "architecture": ("Architecture", "System boundaries, runtime pieces and module dependencies."),
            "features": ("Features", "Capabilities and their entry points in code."),
            "flows": ("Flows", "How selected features work, step by step."),
            "health": ("Health & risks", "Hotspots, knowledge silos, testing and findings."),
            "onboarding": ("Onboarding", "How to run it, what to read first, glossary and starter tasks."),
            "conformance": ("Document conformance", "Claims in the external document checked against the code."),
            "feasibility": ("Feasibility", "Whether what is expected of us can actually be built."),
            "readiness": ("MVP readiness", "MVP scope, the architecture decisions to make, and the work behind them."),
        },
        "derived": "Derived from code", "model": "Model interpretation", "expand": "Expand", "close": "Close",
        "copy": "Copy Mermaid source", "copied": "Copied", "legend_solid": "Solid frame: derived deterministically from code.",
        "legend_dashed": "Dashed frame: the model's reading of the code. Check the references.",
        "missing_ref": "This path was not found in the repo", "missing_refs": "references not found in repo",
        "empty": "Not generated: {file} is missing. Re-run the agent or invoke that subagent on its own.",
        "files": "files", "lines": "lines", "languages": "Languages", "stack": "Tech stack",
        "activity": "Commit activity (last 24 months)", "purpose": "Purpose", "audience": "Audience",
        "commits": "commits", "contributors": "contributors", "tests": "test/source line ratio",
        "context": "Context diagram", "containers": "Runtime containers", "components": "Components",
        "layers": "Layers", "external": "External systems", "datamodel": "Data model", "decisions": "Key decisions",
        "depgraph": "Import graph between directories", "infra": "Infrastructure & ops", "style": "Architecture style",
        "featuremap": "Feature map", "entry": "Entry points", "keyfiles": "Key files",
        "dependson": "Depends on", "flowlinks": "Flows", "confidence": "confidence", "routes": "Detected routes",
        "trigger": "Trigger", "steps": "Steps", "errors": "Error paths", "sidefx": "Side effects", "notes": "Notes",
        "findings": "Findings", "strengths": "Strengths", "hotspots": "Hotspots", "coupling": "Files that change together",
        "busfactor": "Bus factor (knowledge concentration)", "testing": "Testing", "markers": "TODO / FIXME markers",
        "secrets": "Possible secrets (values not captured)", "largest": "Largest files",
        "howtorun": "Run it", "reading": "Reading order", "glossary": "Glossary", "tasks": "Starter & assessment tasks",
        "tasks_note": "Tasks grounded in the real repo: usable for onboarding or for repo-based technical assessments.",
        "acceptance": "Acceptance criteria", "envkeys": "Environment variables (names only)",
        "score": "score", "fixes": "fix commits", "authors": "authors", "shared": "shared commits", "degree": "degree",
        "dir": "dir", "bf": "bus factor", "topauthor": "top author", "share": "share",
        "recommendation": "Recommendation", "gaps": "Gaps", "frameworks": "Test tooling", "why": "Why",
        "generated": "Generated", "nogit": "No git history; activity, hotspots and bus factor were skipped.",
        "noflows": "No flow data.", "flow_for": "Feature",
        "render_err": "Diagram failed to render. Mermaid syntax error:", "offline": "Mermaid failed to load (offline?). Source below.",
    },
}
PAGES = ["index", "architecture", "features", "flows", "health", "onboarding"]
OPTIONAL_PAGES = ["conformance", "feasibility", "readiness"]   # rendered only when their data file exists
ACTIVE: list[str] = list(PAGES)                   # set per language in main()

L["tr"].update({
    "lang_name": "Türkçe", "lang_label": "Dil", "downloads": "Belgeler", "report_title": "Repo Analiz Raporu",
    "report_kicker": "REPO ANALİZİ", "toc": "İçindekiler", "h_exec": "Yönetici özeti", "h_tech": "Teknoloji ve altyapı",
    "h_arch": "Mimari", "h_features": "Özellikler", "h_flows": "Özellik akışları", "h_health": "Kod sağlığı ve riskler",
    "c_doc": "İncelenen doküman",
    "c_claims": "İddia denetimi",
    "c_gaps": "Dokümanda yer almayan bulgular",
    "c_verdict": "Karar",
    "c_docref": "Doküman",
    "c_finding": "Kodun söylediği",
    "c_impact": "Etki",
    "f_expect": "Beklentiler",
    "f_path": "Kritik yol",
    "f_open": "Karar bekleyenler",
    "f_rating": "Değerlendirme",
    "f_prereq": "Ön koşullar",
    "f_effort": "Eforu",
    "f_risk": "Risk",
    "f_who": "Kim karar verir",
    "v_confirmed": "Doğrulandı",
    "v_partially_true": "Kısmen doğru",
    "v_understated": "Eksik anlatılmış",
    "v_contradicted": "Çelişiyor",
    "v_unverifiable": "Doğrulanamadı",
    "r_achievable": "Yapılabilir",
    "r_conditional": "Şartlı yapılabilir",
    "r_at_risk": "Riskli",
    "r_not_achievable": "Bu kapsamda yapılamaz",
    "c_notsot": "Bu doküman bir iddia kümesi olarak ele alınmıştır, doğruluk kaynağı olarak değil.",
    "h_conformance": "Doküman uyumu", "h_feasibility": "Yapılabilirlik", "h_readiness": "MVP hazırlığı",
    "rd_scope": "MVP kapsamı", "rd_decisions": "Karar verilmesi gerekenler", "rd_options": "Seçenekler",
    "rd_reco": "Öneri", "rd_work": "İş kalemleri", "rd_tasks": "Görevler", "rd_seq": "Sıra",
    "rd_open": "David'e sorular", "rd_outcome": "Çıktı", "rd_impact": "Etki",
    "rd_in": "MVP'de", "rd_out": "Kapsam dışı", "rd_later": "Sonraya", "rd_diagrams": "Mimari görünüm",
    "h_onboarding": "Başlangıç rehberi", "h_appendix": "Ekler", "app_routes": "Tespit edilen route'lar",
    "app_method": "Yöntem ve doğrulama", "key_figures": "Temel göstergeler", "top_findings": "Öne çıkan bulgular",
    "metric": "Gösterge", "value": "Değer", "c_name": "Ad", "c_category": "Kategori", "c_role": "Rol",
    "c_refs": "Referans", "c_file": "Dosya", "c_why": "Neden", "c_step": "Adım", "c_command": "Komut",
    "c_term": "Terim", "c_meaning": "Anlamı", "c_actor": "Aktör", "c_action": "İşlem", "c_condition": "Koşul",
    "c_handling": "Ele alınış", "c_kind": "Tür", "c_detail": "Detay", "c_severity": "Önem", "c_finding": "Bulgu",
    "c_feature": "Özellik", "c_summary": "Özet", "c_resp": "Sorumluluk", "c_purpose": "Amaç", "c_desc": "Açıklama",
    "c_method": "Metot", "c_path": "Yol", "c_framework": "Framework", "c_lang": "Dil", "c_share": "Pay",
    "c_entity": "Varlık", "c_marker": "İşaret", "c_conf": "Güven", "c_type": "Tür", "c_diff": "Zorluk",
    "figure": "Şekil", "footer": "Sayfa {page} / {total}", "repository": "Repo", "branch": "Dal",
    "report_lang": "Rapor dili", "features_n": "özellik", "flows_n": "akış", "findings_n": "bulgu",
    "routes_n": "route", "rationale": "Gerekçe", "tradeoffs": "Ödünleşimler", "prov_model": "Model yorumu ({src}); referanslar repoya karşı doğrulandı.",
    "prov_derived": "Koddan deterministik olarak türetildi.", "none": "Yok",
    "verified": "{ok} referans repoya karşı doğrulandı; {bad} referans bulunamadı.",
    "method_p": [
        "Bu rapor iki katmanlı bir süreçle üretildi. Önce LLM kullanmayan bir toplayıcı repoyu taradı: dosyalar, diller, "
        "manifest'ler, framework'ler, route'lar, import grafiği ve git geçmişinden sıcak noktalar, birlikte değişen dosyalar "
        "ve bus factor. Bu bölümler \"koddan türetildi\" olarak işaretlidir ve tekrar çalıştırıldığında aynı sonucu verir.",
        "Ardından uzman subagent'lar (mimari, özellik haritası, akış izleme, kalite denetimi) kodu okuyarak yapılandırılmış "
        "JSON üretti. Bu bölümler \"model yorumu\" olarak işaretlidir. Modelin verdiği her dosya ve satır referansı repoya "
        "karşı kontrol edildi; bulunamayanlar raporda üstü çizili gösterilir ve aşağıda listelenir.",
        "Diyagramlar Mermaid ile çizildi. Rapor, kaynak dildeki analizden çevrilmiş olabilir; çeviri sırasında kimlikler, "
        "dosya yolları, komutlar ve diyagram yapısı değiştirilmez, bu da otomatik olarak doğrulanır.",
    ],
    "missing_list": "Bulunamayan referanslar",
    "enum": {
        "critical": "Kritik", "high": "Yüksek", "medium": "Orta", "low": "Düşük", "info": "Bilgi",
        "security": "Güvenlik", "reliability": "Güvenilirlik", "maintainability": "Bakım kolaylığı",
        "performance": "Performans", "testing": "Test", "docs": "Dokümantasyon", "dependencies": "Bağımlılıklar",
        "operability": "Operasyon", "user-facing": "Kullanıcıya dönük", "admin": "Yönetim", "integration": "Entegrasyon",
        "background": "Arka plan", "infrastructure": "Altyapı", "developer-tooling": "Geliştirici araçları",
        "library-api": "Kütüphane API", "http-route": "HTTP route", "ui-page": "Arayüz sayfası",
        "cli-command": "CLI komutu", "job": "Arka plan işi", "event-handler": "Olay işleyici", "public-api": "Genel API",
        "webhook": "Webhook", "database": "Veritabanı", "queue": "Kuyruk", "cache": "Önbellek", "api": "API",
        "storage": "Depolama", "auth": "Kimlik doğrulama", "email": "E-posta", "payments": "Ödeme", "other": "Diğer",
        "bugfix": "Hata düzeltme", "feature": "Özellik", "refactor": "Refaktör", "test": "Test", "easy": "Kolay",
        "hard": "Zor", "backend": "Backend", "frontend": "Frontend", "Other": "Diğer", "Text": "Metin",
    },
})
L["en"].update({
    "lang_name": "English", "lang_label": "Language", "downloads": "Documents", "report_title": "Repository Analysis Report",
    "report_kicker": "REPOSITORY ANALYSIS", "toc": "Contents", "h_exec": "Executive summary", "h_tech": "Technology and infrastructure",
    "h_arch": "Architecture", "h_features": "Features", "h_flows": "Feature flows", "h_health": "Code health and risks",
    "c_doc": "Document under review",
    "c_claims": "Claim audit",
    "c_gaps": "Findings the document omits",
    "c_verdict": "Verdict",
    "c_docref": "Document",
    "c_finding": "What the code says",
    "c_impact": "Impact",
    "f_expect": "Expectations",
    "f_path": "Critical path",
    "f_open": "Open decisions",
    "f_rating": "Rating",
    "f_prereq": "Prerequisites",
    "f_effort": "Effort",
    "f_risk": "Risk",
    "f_who": "Who decides",
    "v_confirmed": "Confirmed",
    "v_partially_true": "Partially true",
    "v_understated": "Understated",
    "v_contradicted": "Contradicted",
    "v_unverifiable": "Unverifiable",
    "r_achievable": "Achievable",
    "r_conditional": "Achievable with conditions",
    "r_at_risk": "At risk",
    "r_not_achievable": "Not achievable as scoped",
    "c_notsot": "This document is treated as a set of claims, not as a source of truth.",
    "h_conformance": "Document conformance", "h_feasibility": "Feasibility", "h_readiness": "MVP readiness",
    "rd_scope": "MVP scope", "rd_decisions": "Decisions to make", "rd_options": "Options",
    "rd_reco": "Recommendation", "rd_work": "Workstreams", "rd_tasks": "Tasks", "rd_seq": "Sequence",
    "rd_open": "Questions for the other side", "rd_outcome": "Outcome", "rd_impact": "Impact",
    "rd_in": "In MVP", "rd_out": "Out of scope", "rd_later": "Later", "rd_diagrams": "Architecture picture",
    "h_onboarding": "Onboarding", "h_appendix": "Appendices", "app_routes": "Detected routes",
    "app_method": "Method and verification", "key_figures": "Key figures", "top_findings": "Top findings",
    "metric": "Metric", "value": "Value", "c_name": "Name", "c_category": "Category", "c_role": "Role",
    "c_refs": "Reference", "c_file": "File", "c_why": "Why", "c_step": "Step", "c_command": "Command",
    "c_term": "Term", "c_meaning": "Meaning", "c_actor": "Actor", "c_action": "Action", "c_condition": "Condition",
    "c_handling": "Handling", "c_kind": "Kind", "c_detail": "Detail", "c_severity": "Severity", "c_finding": "Finding",
    "c_feature": "Feature", "c_summary": "Summary", "c_resp": "Responsibility", "c_purpose": "Purpose", "c_desc": "Description",
    "c_method": "Method", "c_path": "Path", "c_framework": "Framework", "c_lang": "Language", "c_share": "Share",
    "c_entity": "Entity", "c_marker": "Marker", "c_conf": "Confidence", "c_type": "Type", "c_diff": "Difficulty",
    "figure": "Figure", "footer": "Page {page} of {total}", "repository": "Repository", "branch": "Branch",
    "report_lang": "Report language", "features_n": "features", "flows_n": "flows", "findings_n": "findings",
    "routes_n": "routes", "rationale": "Rationale", "tradeoffs": "Trade-offs", "prov_model": "Model interpretation ({src}); references verified against the repository.",
    "prov_derived": "Derived deterministically from code.", "none": "None",
    "verified": "{ok} references verified against the repository; {bad} not found.",
    "method_p": [
        "This report was produced in two layers. First, a collector that uses no LLM scanned the repository: files, "
        "languages, manifests, frameworks, routes, the import graph, and from git history the hotspots, files that change "
        "together and bus factor. These sections are marked \"derived from code\" and are reproducible.",
        "Then specialist subagents (architecture, feature mapping, flow tracing, quality audit) read the code and wrote "
        "structured JSON. These sections are marked \"model interpretation\". Every file and line reference the model gave "
        "was checked against the repository; references that were not found are struck through and listed below.",
        "Diagrams are drawn with Mermaid. The report may be translated from the source-language analysis; translation "
        "never changes ids, file paths, commands or diagram structure, and this is verified automatically.",
    ],
    "missing_list": "References not found",
    "enum": {
        "critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "info": "Info",
        "security": "Security", "reliability": "Reliability", "maintainability": "Maintainability",
        "performance": "Performance", "testing": "Testing", "docs": "Docs", "dependencies": "Dependencies",
        "operability": "Operability", "user-facing": "User-facing", "admin": "Admin", "integration": "Integration",
        "background": "Background", "infrastructure": "Infrastructure", "developer-tooling": "Developer tooling",
        "library-api": "Library API", "http-route": "HTTP route", "ui-page": "UI page", "cli-command": "CLI command",
        "job": "Job", "event-handler": "Event handler", "public-api": "Public API", "webhook": "Webhook",
        "database": "Database", "queue": "Queue", "cache": "Cache", "api": "API", "storage": "Storage", "auth": "Auth",
        "email": "Email", "payments": "Payments", "other": "Other", "bugfix": "Bug fix", "feature": "Feature",
        "refactor": "Refactor", "test": "Test", "easy": "Easy", "hard": "Hard", "backend": "Backend", "frontend": "Frontend",
    },
})
LANG_ORDER = ["en", "tr"]


def te(t: dict, value) -> str:
    """display label for an English enum value (severity, category, kind, ...)"""
    v = str(value or "")
    return t["enum"].get(v, v)

# --------------------------------------------------------------------------- validation

S = str
SPEC = {
    "overview.json": {"one_liner": S, "purpose": S, "tech_stack": [{"name": S}], "how_to_run": [{"step": S}],
                      "reading_order": [{"ref": S}], "glossary": [{"term": S, "meaning": S}],
                      "starter_tasks": [{"title": S, "description": S}]},
    "architecture.json": {"summary": S, "style": S, "diagrams": {"context": S, "containers": S, "components": S},
                          "layers": [{"name": S, "responsibility": S}], "external_systems": [{"name": S}],
                          "data_model": {"diagram": S, "entities": [{"name": S}]}, "key_decisions": [{"decision": S}]},
    "features.json": {"feature_map": S, "features": [{"id": S, "name": S, "summary": S}]},
    "flows": {"flows": [{"id": S, "feature_id": S, "name": S, "sequence": S, "steps": [{"action": S}]}]},
    "health.json": {"summary": S, "strengths": [{"title": S}], "testing": {"summary": S},
                    "findings": [{"severity": S, "category": S, "title": S, "detail": S}]},
}
MERMAID_KEYS = {"context", "containers", "components", "diagram", "feature_map", "sequence"}
MERMAID_START = re.compile(r"^(flowchart|graph|sequenceDiagram|erDiagram|classDiagram|stateDiagram(-v2)?|mindmap|"
                           r"C4Context|C4Container|C4Component|journey|timeline|block-beta|architecture-beta)\b")
SEVERITIES = ["critical", "high", "medium", "low", "info"]
VERDICTS = ["contradicted", "understated", "partially_true", "unverifiable", "confirmed"]
RATINGS = ["not_achievable", "at_risk", "conditional", "achievable"]


def strip_fences(code: str) -> str:
    code = (code or "").strip()
    code = re.sub(r"^```(?:mermaid)?\s*\n", "", code)
    return re.sub(r"\n?```\s*$", "", code).strip()


def check_shape(obj, spec, where: str, issues: list):
    if isinstance(spec, type):
        if not isinstance(obj, spec):
            issues.append(f"{where}: expected {spec.__name__}, got {type(obj).__name__}")
    elif isinstance(spec, dict):
        if not isinstance(obj, dict):
            issues.append(f"{where}: expected object")
            return
        for k, sub in spec.items():
            if k not in obj:
                issues.append(f"{where}.{k}: missing")
            else:
                check_shape(obj[k], sub, f"{where}.{k}", issues)
    elif isinstance(spec, list):
        if not isinstance(obj, list):
            issues.append(f"{where}: expected list")
            return
        for i, item in enumerate(obj):
            check_shape(item, spec[0], f"{where}[{i}]", issues)


def mermaid_lint(code: str, where: str, issues: list):
    code = strip_fences(code)
    if not code:
        return
    body = re.sub(r"^(%%.*\n)+", "", code).lstrip()
    if not MERMAID_START.match(body):
        issues.append(f"{where}: mermaid must start with a diagram type (flowchart, sequenceDiagram, erDiagram, ...)")
        return
    if body.startswith(("flowchart", "graph")):
        for m in re.finditer(r"\w+\[([^\]\"]*[(){}<>;#][^\]\"]*)\]", body):
            issues.append(f"{where}: label [{m.group(1)[:40]}] has special chars; wrap it in quotes: [\"...\"]")
        if re.search(r"^\s*end\s*\[", body, re.M) or re.search(r"-->\s*end\b", body):
            issues.append(f"{where}: node id 'end' is reserved; rename it")
    if body.startswith("sequenceDiagram"):
        for m in re.finditer(r"^\s*(participant|actor)\s+([^\n]+)$", body, re.M):
            decl = m.group(2)
            if " as " not in decl and " " in decl.strip():
                issues.append(f"{where}: participant '{decl[:40]}' has spaces; use 'participant Id as Label'")


class Refs:
    def __init__(self, facts: dict, repo_url: str | None):
        self.lines = {f["path"]: f["lines"] for f in facts.get("files", [])}
        self.dirs = set()
        for p in self.lines:
            parts = p.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                self.dirs.add("/".join(parts[:i]))
        git = facts.get("git") or {}
        self.web = (repo_url or git.get("remote_web") or "").rstrip("/")
        self.head = git.get("head") or "HEAD"
        self.root = facts.get("repo", {}).get("path", "")
        self.missing: list[str] = []

    @staticmethod
    def parse(ref: str):
        ref = str(ref).strip().strip("`")
        m = re.match(r"^(.*?)(?::(\d+)(?:-(\d+))?)?$", ref)
        path, a, b = m.group(1), m.group(2), m.group(3)
        path = posixpath.normpath(path) if path not in ("", ".") else path
        return path.lstrip("/"), int(a) if a else None, int(b) if b else None

    def status(self, ref: str) -> tuple[bool, str | None]:
        path, a, _ = self.parse(ref)
        if path in self.lines:
            if a and a > self.lines[path] + 1:
                return False, f"line {a} > file length {self.lines[path]}"
            return True, None
        if path.rstrip("/") in self.dirs:
            return True, None
        return False, "path not found"

    def url(self, ref: str) -> str | None:
        path, a, b = self.parse(ref)
        is_dir = path.rstrip("/") in self.dirs and path not in self.lines
        if self.web:
            kind = "tree" if is_dir else "blob"
            sep = "/-/" if "gitlab" in self.web else "/"
            anchor = f"#L{a}" + (f"-L{b}" if b else "") if a and not is_dir else ""
            return f"{self.web}{sep}{kind}/{self.head}/{path}{anchor}"
        if self.root and not is_dir:
            return f"vscode://file/{self.root}/{path}" + (f":{a}" if a else "")
        return None

    def chip(self, ref: str, t: dict) -> str:
        if not ref:
            return ""
        ok, why = self.status(ref)
        label = esc(ref)
        if not ok:
            self.missing.append(ref)
            return f'<span class="ref missing" title="{esc(t["missing_ref"])}: {esc(why)}">{label}</span>'
        url = self.url(ref)
        return f'<a class="ref" href="{esc(url)}">{label}</a>' if url else f'<span class="ref">{label}</span>'

    def chips(self, refs, t) -> str:
        refs = [r for r in (refs or []) if isinstance(r, str) and r.strip()]
        return '<span class="refs">' + " ".join(self.chip(r, t) for r in refs) + "</span>" if refs else ""


def walk_refs(obj, where: str, refs: Refs, issues: list):
    if isinstance(obj, dict):
        for k, v in obj.items():
            w = f"{where}.{k}"
            if k == "ref" and isinstance(v, str) and v.strip():
                ok, why = refs.status(v)
                if not ok:
                    issues.append(f"{w}: '{v}' — {why}")
            elif k in ("refs", "key_files", "evidence_refs") and isinstance(v, list):
                for i, r in enumerate(v):
                    if isinstance(r, str) and r.strip():
                        ok, why = refs.status(r)
                        if not ok:
                            issues.append(f"{w}[{i}]: '{r}' — {why}")
            elif k in MERMAID_KEYS and isinstance(v, str):
                mermaid_lint(v, w, issues)
            else:
                walk_refs(v, w, refs, issues)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk_refs(v, f"{where}[{i}]", refs, issues)


def data_dir(out: Path, lang: str) -> Path:
    d = out / "data" / lang
    return d if d.is_dir() else out / "data"   # legacy single-language layout


def available_langs(out: Path) -> list[str]:
    found = [x for x in LANG_ORDER if (out / "data" / x).is_dir() and any((out / "data" / x).glob("*.json"))]
    return found


def load(out: Path, lang: str) -> tuple[dict, dict]:
    facts = json.loads((out / "facts.json").read_text())
    data, d = {}, data_dir(out, lang)
    for name in ("overview.json", "architecture.json", "features.json", "health.json",
             "conformance.json", "feasibility.json", "readiness.json"):
        p = d / name
        if p.exists():
            try:
                data[name] = json.loads(p.read_text())
            except json.JSONDecodeError as e:
                data[name] = {"__error__": f"invalid JSON: {e}"}
    flows = []
    for p in sorted(d.glob("flows*.json")):
        try:
            flows += json.loads(p.read_text()).get("flows", [])
        except (json.JSONDecodeError, AttributeError) as e:
            data.setdefault("__flow_errors__", []).append(f"{p.name}: {e}")
    if flows or (d / "flows.json").exists():
        data["flows"] = {"flows": flows}
    return facts, data


def validate(facts: dict, data: dict, refs: Refs) -> list[str]:
    issues: list[str] = []
    for name, spec in SPEC.items():
        obj = data.get(name)
        if obj is None:
            issues.append(f"{name}: file missing ({'flows-*.json' if name == 'flows' else name})")
            continue
        if "__error__" in obj:
            issues.append(f"{name}: {obj['__error__']}")
            continue
        check_shape(obj, spec, name, issues)
        walk_refs(obj, name, refs, issues)
    for name in ("conformance.json", "feasibility.json", "readiness.json"):
        obj = data.get(name)
        if obj is None:
            continue
        if "__error__" in obj:
            issues.append(f"{name}: {obj['__error__']}")
            continue
        walk_refs(obj, name, refs, issues)
        for i, c in enumerate(obj.get("claims", [])):
            if isinstance(c, dict) and c.get("verdict") not in VERDICTS:
                issues.append(f"{name}.claims[{i}].verdict: must be one of {VERDICTS}")
        for i, e in enumerate(obj.get("expectations", [])):
            if isinstance(e, dict) and e.get("rating") not in RATINGS:
                issues.append(f"{name}.expectations[{i}].rating: must be one of {RATINGS}")
    issues += data.get("__flow_errors__", [])
    feats = {f.get("id") for f in (data.get("features.json") or {}).get("features", []) if isinstance(f, dict)}
    for i, fl in enumerate((data.get("flows") or {}).get("flows", [])):
        if isinstance(fl, dict) and feats and fl.get("feature_id") not in feats:
            issues.append(f"flows[{i}].feature_id: '{fl.get('feature_id')}' is not a feature id in features.json")
    for i, f in enumerate((data.get("health.json") or {}).get("findings", [])):
        if isinstance(f, dict) and f.get("severity") not in SEVERITIES:
            issues.append(f"health.json.findings[{i}].severity: must be one of {SEVERITIES}")
    return issues


# --------------------------------------------------------------------------- translation parity

NO_TRANSLATE = {"id", "feature_id", "depends_on", "ref", "refs", "key_files", "severity", "category", "confidence",
                "kind", "type", "difficulty", "command"}
PROSE_KEYS = {"summary", "purpose", "audience", "one_liner", "detail", "description", "rationale", "tradeoffs",
              "recommendation", "responsibility", "action", "trigger", "notes", "why", "meaning", "handling",
              "why_it_matters", "style"}
SEQ_BLOCK = re.compile(r"^(alt|else|opt|loop|par|and|critical|break|option|rect|title|accTitle|accDescr)\b")


def mermaid_signature(code: str) -> list[str]:
    """diagram skeleton with every human-readable label removed: ids, arrows and keywords must survive translation"""
    sig = []
    for line in strip_fences(code or "").splitlines():
        line = re.sub(r"%%.*$", "", line).strip()
        if not line:
            continue
        m = SEQ_BLOCK.match(line)
        if m:
            sig.append(m.group(1))
            continue
        line = re.sub(r'"[^"]*"', '"…"', line)
        line = re.sub(r"^((?:participant|actor)\s+\S+)\s+as\s+.*$", r"\1 as …", line)
        if ":" in line and not line.startswith(("click", "classDef", "style", "linkStyle")):
            line = line.split(":", 1)[0].rstrip() + " : …"
        prev = None
        while prev != line:
            prev = line
            line = re.sub(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}|\|[^|]*\|",
                          lambda mm: mm.group(0)[0] + "…" + mm.group(0)[-1], line)
        sig.append(line)
    return sig


def parity(a, b, where: str, issues: list):
    if isinstance(a, dict):
        if not isinstance(b, dict):
            issues.append(f"{where}: must be an object, like the source")
            return
        missing, extra = [k for k in a if k not in b], [k for k in b if k not in a]
        if missing:
            issues.append(f"{where}: keys missing compared to the source: {missing}")
        if extra:
            issues.append(f"{where}: keys not in the source: {extra}")
        for k in a:
            if k not in b:
                continue
            w = f"{where}.{k}"
            if k in NO_TRANSLATE:
                if a[k] != b[k]:
                    issues.append(f"{w}: must be identical to the source (ids, refs, enums and commands are never translated)")
            elif k in MERMAID_KEYS and isinstance(a[k], str):
                sa, sb = mermaid_signature(a[k]), mermaid_signature(b[k] if isinstance(b[k], str) else "")
                if sa != sb:
                    i = next((i for i, (x, y) in enumerate(zip(sa, sb)) if x != y), min(len(sa), len(sb)))
                    got = sb[i] if i < len(sb) else "<missing line>"
                    want = sa[i] if i < len(sa) else "<extra line>"
                    issues.append(f"{w}: diagram structure differs from the source at statement {i + 1}: "
                                  f"expected `{want[:70]}`, got `{got[:70]}` (translate labels only; keep ids, arrows, keywords)")
            elif k in PROSE_KEYS and isinstance(a[k], str) and a[k] == b[k] and len(a[k]) > 60:
                issues.append(f"{w}: looks untranslated (identical to the source)")
            else:
                parity(a[k], b[k], w, issues)
    elif isinstance(a, list):
        if not isinstance(b, list) or len(a) != len(b):
            issues.append(f"{where}: {len(b) if isinstance(b, list) else 'no'} items, source has {len(a)}")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            parity(x, y, f"{where}[{i}]", issues)


def translation_issues(src: dict, dst: dict, src_lang: str, lang: str) -> list[str]:
    issues: list[str] = []
    for name in src:
        if name.startswith("__"):
            continue
        if name not in dst:
            fn = "flows-*.json" if name == "flows" else name
            issues.append(f"{name}: missing translation (data/{src_lang}/{fn} → data/{lang}/{fn})")
            continue
        if "__error__" in src[name] or "__error__" in dst[name]:
            continue
        parity(src[name], dst[name], name, issues)
    return issues


# --------------------------------------------------------------------------- visual helpers

PALETTE = ["#17232C", "#1D6B67", "#A8701A", "#5B7A4E", "#9E3A2F", "#3F5C7A", "#B9A77E", "#6E4E6B"]


def contour_svg(seed: str, w: int = 1200, h: int = 340) -> str:
    rnd = random.Random(hashlib.sha1(seed.encode()).hexdigest())
    cx, cy = rnd.uniform(0.62, 0.82) * w, rnd.uniform(0.35, 0.62) * h
    harmonics = [(k, rnd.uniform(0.05, 0.13), rnd.uniform(0, math.tau)) for k in (2, 3, 5)]
    stretch = rnd.uniform(1.4, 1.9)
    paths = []
    for ring in range(1, 19):
        r = ring * 21
        drift = ring * rnd.uniform(0.01, 0.03)
        pts = []
        for i in range(72):
            t = math.tau * i / 72
            rr = r * (1 + sum(a * math.sin(k * t + p + drift) for k, a, p in harmonics))
            pts.append(f"{cx + rr * math.cos(t) * stretch:.1f},{cy + rr * math.sin(t):.1f}")
        cls = "idx" if ring % 5 == 0 else ""
        paths.append(f'<path class="{cls}" d="M{" L".join(pts)} Z"/>')
    peak = f'<path class="peak" d="M{cx:.0f},{cy - 7:.0f} l6,11 h-12 z"/>'
    return (f'<svg class="contours" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid slice" aria-hidden="true">'
            + "".join(paths) + peak + "</svg>")


def mermaid_id(label: str, cache: dict) -> str:
    if label not in cache:
        cache[label] = f"n{len(cache)}"
    return cache[label]


def dep_graph_mermaid(facts: dict, limit: int = 40) -> str:
    edges = facts.get("dependency_graph", {}).get("dir_edges", [])[:limit]
    if not edges:
        return ""
    ids: dict = {}
    lines = ["flowchart LR"]
    for e in edges:
        a, b = mermaid_id(e["from"], ids), mermaid_id(e["to"], ids)
        lines.append(f'  {a} -->|{e["weight"]}| {b}')
    for label, nid in ids.items():
        lines.insert(1, f'  {nid}["{label.replace(chr(34), "")}"]')
    return "\n".join(lines)


def activity_svg(months: list[dict]) -> str:
    if not months:
        return ""
    counts = {m["month"]: m["commits"] for m in months}
    y, mo = map(int, months[-1]["month"].split("-"))
    cal = []
    for _ in range(24):  # fixed 24-month calendar so sparse history doesn't turn into one giant bar
        cal.append(f"{y:04d}-{mo:02d}")
        y, mo = (y, mo - 1) if mo > 1 else (y - 1, 12)
    months = [{"month": k, "commits": counts.get(k, 0)} for k in reversed(cal)]
    w, h, pad = 720, 120, 18
    mx = max(m["commits"] for m in months) or 1
    bw = (w - pad * 2) / len(months)
    bars = []
    for i, m in enumerate(months):
        bh = (h - pad * 2) * m["commits"] / mx
        x, y = pad + i * bw, h - pad - bh
        bars.append(f'<rect x="{x + 1:.1f}" y="{y:.1f}" width="{max(bw - 3, 1):.1f}" height="{bh:.1f}">'
                    f'<title>{esc(m["month"])}: {m["commits"]}</title></rect>')
    return (f'<svg class="activity" viewBox="0 0 {w} {h}" role="img" aria-label="monthly commits">'
            f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}"/>' + "".join(bars) +
            f'<text x="{pad}" y="{h - 3}">{esc(months[0]["month"])}</text>'
            f'<text x="{w - pad}" y="{h - 3}" text-anchor="end">{esc(months[-1]["month"])}</text>'
            f'<text x="{pad}" y="12">max {mx}</text></svg>')


# --------------------------------------------------------------------------- page building blocks


class Page:
    def __init__(self, key: str, facts: dict, t: dict, lang: str, refs: Refs,
                 langs: list[str] | None = None, downloads: list[tuple[str, str]] | None = None):
        self.key, self.facts, self.t, self.lang, self.refs = key, facts, t, lang, refs
        self.langs, self.downloads = langs or [lang], downloads or []
        self.parts: list[str] = []
        self.has_mermaid = False

    def add(self, s: str):
        self.parts.append(s)

    def plate(self, code: str, caption: str, derived: bool, source: str = "") -> str:
        code = strip_fences(code)
        if not code:
            return ""
        self.has_mermaid = True
        t = self.t
        prov = t["derived"] if derived else f'{t["model"]}{": " + esc(source) if source else ""}'
        return (f'<figure class="plate {"derived" if derived else "model"}">'
                f'<div class="mmd-out" role="img" aria-label="{esc(caption)}"></div>'
                f'<pre class="mmd" hidden>{esc(code)}</pre>'
                f'<figcaption><span><strong>{esc(caption)}</strong> <span class="prov">{prov}</span></span>'
                f'<span class="plate-actions"><button type="button" data-act="expand">{t["expand"]}</button>'
                f'<button type="button" data-act="copy">{t["copy"]}</button></span></figcaption></figure>')

    def section(self, title: str, body: str, sid: str = "") -> str:
        if not body.strip():
            return ""
        return f'<section{f" id={chr(34)}{sid}{chr(34)}" if sid else ""}><h2>{esc(title)}</h2>{body}</section>'

    def empty(self, file: str) -> str:
        return f'<p class="empty">{esc(self.t["empty"].format(file="data/" + file))}</p>'

    def html(self) -> str:
        f, t = self.facts, self.t
        name = f["repo"]["name"]
        git = f.get("git") or {}
        nav = "".join(
            f'<a href="{p}.html"{" aria-current=page" if p == self.key else ""}>{esc(t["pages"][p][0])}</a>' for p in ACTIVE)
        meta = [f'{t["generated"]} {esc(f["generated_at"][:10])}']
        if git.get("available"):
            meta.append(f'<span class="mono">{esc(git["head"][:10])}</span> {esc(git.get("branch", ""))}')
        title, lede = t["pages"][self.key]
        head = "" if self.key == "index" else (
            f'<header class="page-head"><h1>{esc(title)}</h1><p class="lede">{esc(lede)}</p></header>')
        missing_note = ""
        if self.refs.missing:
            missing_note = (f'<p class="warn-note">{len(set(self.refs.missing))} {esc(t["missing_refs"])}: '
                            + " ".join(f'<span class="ref missing">{esc(r)}</span>' for r in sorted(set(self.refs.missing))[:12]) + "</p>")
        switch = ""
        if len(self.langs) > 1:
            switch = '<p class="langs">' + " · ".join(
                f'<a href="../{x}/{self.key}.html" hreflang="{x}"{" aria-current=true" if x == self.lang else ""}>'
                f'{esc(L[x]["lang_name"])}</a>' for x in self.langs) + "</p>"
        dl = ""
        if self.downloads:
            dl = (f'<p class="dl"><span>{esc(t["downloads"])}:</span> '
                  + " ".join(f'<a href="{esc(fn)}" download>{esc(label)}</a>' for label, fn in self.downloads) + "</p>")
        script = MERMAID_JS.replace("__T__", json.dumps({k: t[k] for k in ("render_err", "offline", "copied", "close", "expand")})) \
            if self.has_mermaid else ""
        return f"""<!doctype html>
<html lang="{self.lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — {esc(name)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,300..800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body><aside class="atlas-index"><a class="repo-name" href="index.html">{esc(name)}</a>
<p class="meta">{"<br>".join(meta)}</p>{switch}{dl}<nav aria-label="report pages">{nav}</nav>
<div class="legend"><p><span class="sw solid"></span>{esc(t["legend_solid"])}</p><p><span class="sw dashed"></span>{esc(t["legend_dashed"])}</p></div></aside>
<main>{head}{"".join(self.parts)}{missing_note}</main>{script}</body></html>"""


# --------------------------------------------------------------------------- pages


def page_index(pg: Page, data: dict):
    f, t, ov = pg.facts, pg.t, data.get("overview.json") or {}
    tot, git = f["totals"], f.get("git") or {}
    stats = [f'{tot["files"]:,} {t["files"]}', f'{tot["lines"]:,} {t["lines"]}', f'{len(f["languages"])} {t["languages"].lower()}']
    if git.get("available"):
        stats += [f'{git["total_commits"]:,} {t["commits"]}', f'{git.get("authors_in_window", 0)} {t["contributors"]}']
    stats.append(f'{tot["test_ratio"]} {t["tests"]}')
    pg.add(f'<header class="hero">{contour_svg(f["repo"]["name"])}<div class="hero-text">'
           f'<h1>{esc(f["repo"]["name"])}</h1><p class="one-liner">{esc(ov.get("one_liner", ""))}</p>'
           f'<p class="stats">{" / ".join(esc(s) for s in stats)}</p></div></header>')
    if ov:
        body = f'<p class="prose">{esc(ov.get("purpose", ""))}</p>'
        if ov.get("audience"):
            body += f'<p class="prose"><strong>{esc(t["audience"])}:</strong> {esc(ov["audience"])}</p>'
        pg.add(pg.section(t["purpose"], body))
    else:
        pg.add(pg.empty("overview.json"))

    # tech stack: model-described + code-detected
    rows = ""
    for s in ov.get("tech_stack", []):
        rows += (f'<tr><td>{esc(s.get("name"))}</td><td>{esc(te(t, s.get("category", "")))}</td>'
                 f'<td>{esc(s.get("role", ""))}</td><td>{pg.refs.chips(s.get("refs"), t)}</td></tr>')
    detected = ", ".join(f'{esc(x["name"])} <span class="muted">({esc(x["category"])})</span>' for x in f["frameworks"])
    body = (f'<table><tbody>{rows}</tbody></table>' if rows else "") + \
           (f'<p class="detected"><span class="prov">{t["derived"]}:</span> {detected}</p>' if detected else "")
    pg.add(pg.section(t["stack"], body))

    langs = [l for l in f["languages"] if l["lines"] > 0][:8]
    total = sum(l["lines"] for l in langs) or 1
    bar = "".join(f'<span style="width:{100 * l["lines"] / total:.2f}%;background:{PALETTE[i % 8]}" '
                  f'title="{esc(l["lang"])}"></span>' for i, l in enumerate(langs))
    legend = "".join(f'<li><span class="sw" style="background:{PALETTE[i % 8]}"></span>{esc(l["lang"])} '
                     f'<span class="muted">{l["lines"]:,} {t["lines"]}, {l["files"]} {t["files"]}</span></li>'
                     for i, l in enumerate(langs))
    pg.add(pg.section(t["languages"], f'<div class="langbar">{bar}</div><ul class="lang-legend">{legend}</ul>'))
    if git.get("available"):
        pg.add(pg.section(t["activity"], activity_svg(git.get("monthly_commits", []))))
    else:
        pg.add(f'<p class="empty">{esc(t["nogit"])}</p>')
    toc = "".join(f'<li><a href="{p}.html">{esc(t["pages"][p][0])}</a> <span class="muted">{esc(t["pages"][p][1])}</span></li>'
                  for p in ACTIVE[1:])
    pg.add(f'<section><ul class="toc">{toc}</ul></section>')


def page_architecture(pg: Page, data: dict):
    t, a = pg.t, data.get("architecture.json")
    if not a:
        pg.add(pg.empty("architecture.json"))
    else:
        pg.add(f'<p class="prose">{esc(a.get("summary", ""))}</p>')
        if a.get("style"):
            pg.add(f'<p class="prose"><strong>{esc(t["style"])}:</strong> {esc(a["style"])}</p>')
        d = a.get("diagrams", {})
        for key in ("context", "containers", "components"):
            pg.add(pg.plate(d.get(key, ""), t[key], False, "repo-architect"))
        rows = "".join(f'<tr><td>{esc(l.get("name"))}</td><td>{esc(l.get("responsibility"))}</td>'
                       f'<td>{pg.refs.chips(l.get("refs"), t)}</td></tr>' for l in a.get("layers", []))
        pg.add(pg.section(t["layers"], f"<table><tbody>{rows}</tbody></table>" if rows else ""))
        rows = "".join(f'<tr><td>{esc(x.get("name"))}</td><td>{esc(te(t, x.get("kind", "")))}</td><td>{esc(x.get("purpose", ""))}</td>'
                       f'<td>{pg.refs.chips(x.get("refs"), t)}</td></tr>' for x in a.get("external_systems", []))
        pg.add(pg.section(t["external"], f"<table><tbody>{rows}</tbody></table>" if rows else ""))
    graph = dep_graph_mermaid(pg.facts)
    if graph:
        pg.add(pg.section(t["depgraph"], pg.plate(graph, t["depgraph"], True)))
    if a:
        dm = a.get("data_model") or {}
        ents = "".join(f'<tr><td>{esc(e.get("name"))}</td><td>{esc(e.get("description", ""))}</td>'
                       f'<td>{pg.refs.chips(e.get("refs"), t)}</td></tr>' for e in dm.get("entities", []))
        pg.add(pg.section(t["datamodel"], pg.plate(dm.get("diagram", ""), t["datamodel"], False, "repo-architect")
                          + (f"<table><tbody>{ents}</tbody></table>" if ents else "")))
        decs = "".join(f'<div class="decision"><h3>{esc(x.get("decision"))}</h3><p>{esc(x.get("rationale", ""))}</p>'
                       + (f'<p class="muted">{esc(x.get("tradeoffs"))}</p>' if x.get("tradeoffs") else "")
                       + pg.refs.chips(x.get("refs"), t) + "</div>" for x in a.get("key_decisions", []))
        pg.add(pg.section(t["decisions"], decs))
    inf = pg.facts["infra"]
    items = [f'<li>Docker: {pg.refs.chip(d["path"], t)} FROM <code>{esc(", ".join(d["from"]))}</code></li>' for d in inf["dockerfiles"][:6]]
    items += [f'<li>Compose {pg.refs.chip(c["path"], t)}: {esc(", ".join(c["services"]))}</li>' for c in inf["compose_services"][:3]]
    items += [f'<li>CI {pg.refs.chip(c["path"], t)} <span class="muted">{esc(c["system"])}</span></li>' for c in inf["ci"][:10]]
    if inf["k8s_kinds"]:
        items.append(f'<li>Kubernetes: {esc(", ".join(f"{k} ×{v}" for k, v in inf["k8s_kinds"].items()))}</li>')
    if inf["terraform_resources"]:
        items.append(f'<li>Terraform: {esc(", ".join(f"{k} ×{v}" for k, v in list(inf["terraform_resources"].items())[:12]))}</li>')
    pg.add(pg.section(t["infra"], f'<ul class="plain">{"".join(items)}</ul>' if items else ""))


def page_features(pg: Page, data: dict):
    t, fs = pg.t, data.get("features.json")
    flows_by_feat: dict = {}
    for fl in (data.get("flows") or {}).get("flows", []):
        flows_by_feat.setdefault(fl.get("feature_id"), []).append(fl)
    if not fs:
        pg.add(pg.empty("features.json"))
    else:
        pg.add(pg.plate(fs.get("feature_map", ""), t["featuremap"], False, "feature-mapper"))
        names = {x.get("id"): x.get("name") for x in fs.get("features", [])}
        toc = "".join(f'<li><a href="#f-{esc(x.get("id"))}">{esc(x.get("name"))}</a></li>' for x in fs.get("features", []))
        pg.add(f'<nav class="inpage"><ul>{toc}</ul></nav>')
        for x in fs.get("features", []):
            eps = "".join(f'<li>{pg.refs.chip(e.get("ref", ""), t)} <span class="kind">{esc(te(t, e.get("kind", "")))}</span> '
                          f'{esc(e.get("detail", ""))}</li>' for e in x.get("entry_points", []))
            deps = ", ".join(f'<a href="#f-{esc(d)}">{esc(names.get(d, d))}</a>' for d in x.get("depends_on", []))
            fl = ", ".join(f'<a href="flows.html#flow-{esc(f.get("id"))}">{esc(f.get("name"))}</a>' for f in flows_by_feat.get(x.get("id"), []))
            pg.add(f'<section class="feature conf-{esc(x.get("confidence", "medium"))}" id="f-{esc(x.get("id"))}">'
                   f'<h2>{esc(x.get("name"))}</h2><p class="muted">{esc(te(t, x.get("category", "")))}, '
                   f'{esc(t["confidence"])}: {esc(te(t, x.get("confidence", "?")))}</p><p class="prose">{esc(x.get("summary"))}</p>'
                   + (f'<h3>{t["entry"]}</h3><ul class="plain">{eps}</ul>' if eps else "")
                   + (f'<h3>{t["keyfiles"]}</h3>{pg.refs.chips(x.get("key_files"), t)}' if x.get("key_files") else "")
                   + (f'<p><strong>{t["dependson"]}:</strong> {deps}</p>' if deps else "")
                   + (f'<p><strong>{t["flowlinks"]}:</strong> {fl}</p>' if fl else "") + "</section>")
    routes = pg.facts.get("routes", [])
    if routes:
        rows = "".join(f'<tr><td class="mono">{esc(r["method"])}</td><td class="mono">{esc(r["path"])}</td>'
                       f'<td>{pg.refs.chip(r["ref"], t)}</td><td class="muted">{esc(r["framework"])}</td></tr>' for r in routes[:200])
        pg.add(pg.section(f'{t["routes"]} ({len(routes)})', f'<p class="prov">{t["derived"]}</p><table><tbody>{rows}</tbody></table>'))


def page_flows(pg: Page, data: dict):
    t = pg.t
    flows = (data.get("flows") or {}).get("flows", [])
    if not flows:
        pg.add(pg.empty("flows*.json"))
        return
    names = {x.get("id"): x.get("name") for x in (data.get("features.json") or {}).get("features", [])}
    toc = "".join(f'<li><a href="#flow-{esc(f.get("id"))}">{esc(f.get("name"))}</a></li>' for f in flows)
    pg.add(f'<nav class="inpage"><ul>{toc}</ul></nav>')
    for fl in flows:
        steps = "".join(f'<li><strong>{esc(s.get("actor", ""))}</strong> {esc(s.get("action"))} {pg.refs.chip(s.get("ref", ""), t)}</li>'
                        for s in fl.get("steps", []))
        errs = "".join(f'<tr><td>{esc(e.get("condition"))}</td><td>{esc(e.get("handling"))}</td><td>{pg.refs.chip(e.get("ref", ""), t)}</td></tr>'
                       for e in fl.get("error_paths", []))
        fx = "".join(f"<li>{esc(s)}</li>" for s in fl.get("side_effects", []))
        feat = fl.get("feature_id")
        pg.add(f'<section class="flow" id="flow-{esc(fl.get("id"))}"><h2>{esc(fl.get("name"))}</h2>'
               + (f'<p class="muted">{t["flow_for"]}: <a href="features.html#f-{esc(feat)}">{esc(names.get(feat, feat))}</a></p>' if feat else "")
               + f'<p class="prose"><strong>{t["trigger"]}:</strong> {esc(fl.get("trigger", ""))}</p>'
               + pg.plate(fl.get("sequence", ""), fl.get("name", ""), False, "flow-tracer")
               + (f'<h3>{t["steps"]}</h3><ol class="steps">{steps}</ol>' if steps else "")
               + (f'<h3>{t["errors"]}</h3><table><tbody>{errs}</tbody></table>' if errs else "")
               + (f'<h3>{t["sidefx"]}</h3><ul class="plain">{fx}</ul>' if fx else "")
               + (f'<h3>{t["notes"]}</h3><p class="prose">{esc(fl.get("notes"))}</p>' if fl.get("notes") else "") + "</section>")


def page_health(pg: Page, data: dict):
    t, f, h = pg.t, pg.facts, data.get("health.json")
    git = f.get("git") or {}
    if h:
        pg.add(f'<p class="prose">{esc(h.get("summary", ""))}</p>')
        findings = sorted(h.get("findings", []), key=lambda x: SEVERITIES.index(x.get("severity")) if x.get("severity") in SEVERITIES else 9)
        blocks = "".join(
            f'<article class="finding sev-{esc(x.get("severity"))}"><h3>{esc(x.get("title"))}</h3>'
            f'<p class="muted">{esc(te(t, x.get("severity")))}, {esc(te(t, x.get("category", "")))}</p><p>{esc(x.get("detail"))}</p>'
            + (f'<p><strong>{t["recommendation"]}:</strong> {esc(x.get("recommendation"))}</p>' if x.get("recommendation") else "")
            + pg.refs.chips(x.get("refs"), t) + "</article>" for x in findings)
        pg.add(pg.section(t["findings"], blocks))
        st = "".join(f'<li><strong>{esc(x.get("title"))}</strong> {esc(x.get("detail", ""))} {pg.refs.chips(x.get("refs"), t)}</li>'
                     for x in h.get("strengths", []))
        pg.add(pg.section(t["strengths"], f'<ul class="plain">{st}</ul>' if st else ""))
    else:
        pg.add(pg.empty("health.json"))

    if f.get("hotspots"):
        notes = {n.get("ref"): n.get("why_it_matters") for n in (h or {}).get("hotspot_notes", [])}
        rows = "".join(
            f'<tr><td>{pg.refs.chip(x["path"], t)}{"<br><span class=muted>" + esc(notes[x["path"]]) + "</span>" if notes.get(x["path"]) else ""}</td>'
            f'<td class="num">{x["commits"]}</td><td class="num">{x["lines"]}</td><td class="num">{x["fix_commits"]}</td>'
            f'<td class="num">{x["authors"]}</td><td class="barcell"><span style="width:{x["score"] * 100:.0f}%"></span></td></tr>'
            for x in f["hotspots"][:20])
        head = (f'<thead><tr><th></th><th>{t["commits"]}</th><th>{t["lines"]}</th><th>{t["fixes"]}</th>'
                f'<th>{t["authors"]}</th><th>{t["score"]}</th></tr></thead>')
        pg.add(pg.section(t["hotspots"], f'<p class="prov">{t["derived"]}</p><table>{head}<tbody>{rows}</tbody></table>'))
    if git.get("coupling"):
        rows = "".join(f'<tr><td>{pg.refs.chip(c["a"], t)}</td><td>{pg.refs.chip(c["b"], t)}</td><td class="num">{c["shared_commits"]}</td>'
                       f'<td class="num">{c["degree"]}</td></tr>' for c in git["coupling"][:15])
        pg.add(pg.section(t["coupling"], f'<p class="prov">{t["derived"]}</p><table><thead><tr><th></th><th></th>'
                                         f'<th>{t["shared"]}</th><th>{t["degree"]}</th></tr></thead><tbody>{rows}</tbody></table>'))
    if git.get("bus_factor"):
        rows = "".join(f'<tr><td class="mono">{esc(b["dir"])}</td><td class="num">{b["bus_factor"]}</td><td>{esc(b["top_author"])}</td>'
                       f'<td class="num">{b["top_share"]:.0%}</td><td class="num">{b["authors"]}</td></tr>' for b in git["bus_factor"][:15])
        pg.add(pg.section(t["busfactor"], f'<p class="prov">{t["derived"]}</p><table><thead><tr><th>{t["dir"]}</th><th>{t["bf"]}</th>'
                                          f'<th>{t["topauthor"]}</th><th>{t["share"]}</th><th>{t["authors"]}</th></tr></thead><tbody>{rows}</tbody></table>'))
    elif not git.get("available"):
        pg.add(f'<p class="empty">{esc(t["nogit"])}</p>')
    tst = (h or {}).get("testing") or {}
    tot = f["totals"]
    body = (f'<p class="prose">{tot["test_files"]} test {t["files"]}, {tot["test_lines"]:,} / {tot["source_lines"]:,} {t["lines"]} '
            f'({t["tests"]}: {tot["test_ratio"]})</p>')
    if tst:
        body += f'<p class="prose">{esc(tst.get("summary", ""))}</p>'
        if tst.get("frameworks"):
            body += f'<p><strong>{t["frameworks"]}:</strong> {esc(", ".join(tst["frameworks"]))}</p>'
        gaps = "".join(f'<li>{esc(g.get("area"))} {pg.refs.chips(g.get("refs"), t)}</li>' for g in tst.get("gaps", []))
        body += f'<h3>{t["gaps"]}</h3><ul class="plain">{gaps}</ul>' if gaps else ""
    pg.add(pg.section(t["testing"], body))
    mk = f["markers"]
    if mk["total"]:
        rows = "".join(f'<li>{pg.refs.chip(x["ref"], t)} <code>{esc(x["text"])}</code></li>' for x in mk["samples"][:25])
        pg.add(pg.section(f'{t["markers"]} ({mk["total"]})', f'<ul class="plain">{rows}</ul>'))
    if f.get("secrets_candidates"):
        rows = "".join(f'<li>{pg.refs.chip(s["ref"], t)} <span class="muted">{esc(s["kind"])}</span></li>' for s in f["secrets_candidates"][:25])
        pg.add(pg.section(t["secrets"], f'<ul class="plain">{rows}</ul>'))
    rows = "".join(f'<tr><td>{pg.refs.chip(x["path"], t)}</td><td class="num">{x["lines"]:,}</td></tr>' for x in f["largest_files"][:12])
    pg.add(pg.section(t["largest"], f"<table><tbody>{rows}</tbody></table>"))


def page_onboarding(pg: Page, data: dict):
    t, ov = pg.t, data.get("overview.json")
    if not ov:
        pg.add(pg.empty("overview.json"))
    else:
        steps = "".join(f'<li>{esc(s.get("step"))}' + (f'<pre><code>{esc(s["command"])}</code></pre>' if s.get("command") else "")
                        + f' {pg.refs.chip(s.get("ref", ""), t)}</li>' for s in ov.get("how_to_run", []))
        pg.add(pg.section(t["howtorun"], f'<ol class="steps">{steps}</ol>' if steps else ""))
        rd = "".join(f'<li>{pg.refs.chip(r.get("ref", ""), t)} {esc(r.get("why", ""))}</li>' for r in ov.get("reading_order", []))
        pg.add(pg.section(t["reading"], f'<ol class="steps">{rd}</ol>' if rd else ""))
        gl = "".join(f'<dt>{esc(g.get("term"))}</dt><dd>{esc(g.get("meaning"))} {pg.refs.chips(g.get("refs"), t)}</dd>'
                     for g in ov.get("glossary", []))
        pg.add(pg.section(t["glossary"], f"<dl>{gl}</dl>" if gl else ""))
        tasks = "".join(
            f'<article class="task"><h3>{esc(x.get("title"))}</h3><p class="muted">{esc(te(t, x.get("type", "")))}, {esc(te(t, x.get("difficulty", "")))}</p>'
            f'<p>{esc(x.get("description"))}</p>{pg.refs.chips(x.get("refs"), t)}'
            + (f'<h4>{t["acceptance"]}</h4><ul class="plain">' + "".join(f"<li>{esc(c)}</li>" for c in x.get("acceptance", [])) + "</ul>"
               if x.get("acceptance") else "") + "</article>" for x in ov.get("starter_tasks", []))
        pg.add(pg.section(t["tasks"], (f'<p class="prose muted">{esc(t["tasks_note"])}</p>' + tasks) if tasks else ""))
    envs = pg.facts["infra"]["env_keys"]
    if envs:
        body = "".join(f'<p>{pg.refs.chip(e["path"], t)}</p><p class="mono wrap">{esc(", ".join(e["keys"]))}</p>' for e in envs[:3])
        pg.add(pg.section(t["envkeys"], body))


# --------------------------------------------------------------------------- assets

CSS = r"""
:root{--paper:#EDEFE9;--sheet:#FBFCFA;--ink:#17232C;--ink-2:#4A5A60;--rule:#C9D0C8;--contour:#B7C2B8;
--ochre:#A8701A;--teal:#1D6B67;--brick:#9E3A2F;--moss:#5B7A4E;
--sans:"Archivo",system-ui,-apple-system,"Segoe UI",sans-serif;--mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}
html{background:var(--paper);color:var(--ink);font:16px/1.55 var(--sans);font-stretch:100%}
body{margin:0;display:grid;grid-template-columns:250px minmax(0,1fr);min-height:100vh}
a{color:var(--teal);text-underline-offset:.18em}
a:focus-visible,button:focus-visible{outline:2px solid var(--ochre);outline-offset:2px}
.atlas-index{position:sticky;top:0;height:100vh;overflow:auto;padding:2rem 1.25rem;border-right:1.5px solid var(--ink);background:var(--paper)}
.repo-name{display:block;font-weight:800;font-stretch:118%;font-size:1.35rem;line-height:1.1;color:var(--ink);text-decoration:none;word-break:break-word}
.meta{font-size:.8rem;color:var(--ink-2);margin:.6rem 0 1.6rem}
.langs{font-size:.85rem;margin:-.9rem 0 1.1rem}.langs a[aria-current]{font-weight:700;color:var(--ink);text-decoration:none}
.dl{font-size:.85rem;margin:-.4rem 0 1.4rem}.dl span{color:var(--ink-2)}.dl a{margin-right:.6rem}
.atlas-index nav{display:flex;flex-direction:column;border-top:1px solid var(--rule)}
.atlas-index nav a{padding:.55rem 0;border-bottom:1px solid var(--rule);color:var(--ink);text-decoration:none;font-stretch:108%}
.atlas-index nav a:hover{color:var(--teal)}
.atlas-index nav a[aria-current=page]{font-weight:700;box-shadow:inset 3px 0 0 var(--ochre);padding-left:.6rem}
.legend{margin-top:2rem;font-size:.78rem;color:var(--ink-2)}
.legend p{display:flex;gap:.5rem;align-items:flex-start;margin:.5rem 0}
.sw{flex:none;display:inline-block;width:14px;height:14px;margin-top:.15rem}
.sw.solid{border:1.5px solid var(--ink)}.sw.dashed{border:1.5px dashed var(--ink-2)}
main{padding:2.5rem clamp(1.25rem,4vw,3.5rem) 5rem;max-width:1180px}
.page-head{border-bottom:1.5px solid var(--ink);padding-bottom:1rem;margin-bottom:1.5rem}
h1{font-weight:800;font-stretch:125%;font-size:clamp(2rem,4.2vw,3.1rem);line-height:1.02;letter-spacing:-.01em;margin:0 0 .4rem}
h2{font-weight:700;font-stretch:112%;font-size:1.4rem;line-height:1.2;margin:2.6rem 0 .8rem}
h3{font-weight:700;font-stretch:104%;font-size:1.02rem;margin:1.4rem 0 .4rem}
h4{font-size:.92rem;margin:1rem 0 .3rem}
.lede{color:var(--ink-2);margin:0;max-width:68ch}
.prose,p{max-width:72ch}
.muted{color:var(--ink-2);font-size:.9em}
.mono,code,pre{font-family:var(--mono);font-size:.84em}
pre code{font-size:1em}
.wrap{word-break:break-word}
pre{background:var(--sheet);border:1px solid var(--rule);padding:.6rem .8rem;overflow:auto;max-width:72ch}
.hero{position:relative;margin:-2.5rem calc(-1*clamp(1.25rem,4vw,3.5rem)) 1rem;padding:4.5rem clamp(1.25rem,4vw,3.5rem) 2.2rem;border-bottom:1.5px solid var(--ink);overflow:hidden;min-height:300px}
.contours{position:absolute;inset:0;width:100%;height:100%}
.contours path{fill:none;stroke:var(--contour);stroke-width:1;stroke-linejoin:round}
.contours path.idx{stroke:#93A294;stroke-width:1.8}
.contours path.peak{fill:var(--ochre);stroke:none}
.hero-text{position:relative;max-width:62ch}
.hero h1{font-size:clamp(2.6rem,7vw,5.2rem);background:linear-gradient(var(--paper),var(--paper)) no-repeat;background-size:100% 100%;display:inline;box-decoration-break:clone;-webkit-box-decoration-break:clone;padding:0 .15em 0 0}
.one-liner{font-size:1.2rem;font-stretch:104%;margin:1rem 0 .6rem;background:var(--paper);display:inline-block;padding:.1rem .3rem .1rem 0}
.stats{color:var(--ink-2);font-size:.92rem;background:var(--paper);display:inline-block;padding:.1rem .3rem .1rem 0}
figure.plate{margin:1.4rem 0;background:var(--sheet);border:1.5px solid var(--ink);padding:1.1rem 1.1rem .7rem}
figure.plate.model{border-style:dashed;border-color:var(--ink-2)}
.mmd-out{overflow:auto;min-height:40px}
.mmd-out svg{display:block;margin:0 auto;max-width:100%;height:auto}
figcaption{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;margin-top:.8rem;padding-top:.6rem;border-top:1px solid var(--rule);font-size:.85rem}
.prov{color:var(--ink-2);font-size:.82rem}
.plate-actions{display:flex;gap:.4rem}
button{font:inherit;font-size:.8rem;background:none;border:1px solid var(--ink-2);color:var(--ink);padding:.2rem .55rem;cursor:pointer}
button:hover{background:var(--ink);color:var(--sheet)}
figure.plate.is-open{position:fixed;inset:3vh 3vw;z-index:50;margin:0;overflow:auto;box-shadow:0 0 0 100vmax rgba(23,35,44,.6)}
figure.plate.is-open .mmd-out svg{max-width:none}
.mmd-err{border-left:3px solid var(--brick);padding:.4rem .8rem;color:var(--brick);font-size:.9rem}
.plate pre.mmd:not([hidden]){margin-top:.6rem;max-width:none}
table{border-collapse:collapse;width:100%;margin:.6rem 0;font-size:.92rem}
th{text-align:left;font-weight:600;font-size:.82rem;color:var(--ink-2);border-bottom:1.5px solid var(--ink);padding:.35rem .5rem}
td{border-bottom:1px solid var(--rule);padding:.45rem .5rem;vertical-align:top}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.barcell{width:120px}.barcell span{display:block;height:8px;background:var(--brick);margin-top:.45rem}
.ref{font-family:var(--mono);font-size:.78rem;padding:.05rem .35rem;border:1px solid var(--rule);background:var(--sheet);text-decoration:none;color:var(--teal);white-space:nowrap;display:inline-block;margin:.1rem 0}
a.ref:hover{border-color:var(--teal)}
.ref.missing{color:var(--brick);border-style:dashed;border-color:var(--brick);text-decoration:line-through}
.refs{display:inline-flex;flex-wrap:wrap;gap:.3rem}
.langbar{display:flex;height:14px;border:1.5px solid var(--ink);max-width:720px}
.langbar span{display:block;height:100%}
.lang-legend{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:.4rem 1.4rem;font-size:.9rem;max-width:720px}
.lang-legend li{display:flex;gap:.4rem;align-items:center}
.activity{width:100%;max-width:720px;height:auto}
.activity rect{fill:var(--teal)}.activity line{stroke:var(--ink)}.activity text{font:11px var(--sans);fill:var(--ink-2)}
ul.plain{padding-left:1.1rem;max-width:80ch}ul.plain li{margin:.3rem 0}
ol.steps{padding-left:1.4rem;max-width:80ch}ol.steps li{margin:.5rem 0}
ol.steps li::marker{font-weight:700;color:var(--ochre)}
.toc{list-style:none;padding:0;border-top:1.5px solid var(--ink);max-width:760px}
.toc li{padding:.7rem 0;border-bottom:1px solid var(--rule);display:flex;flex-wrap:wrap;gap:.3rem 1rem;align-items:baseline}
.toc a{font-weight:700;font-stretch:110%;font-size:1.05rem}
nav.inpage ul{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:.3rem .9rem;font-size:.92rem;border-bottom:1px solid var(--rule);padding-bottom:.8rem}
section.feature,section.flow{border-top:1.5px solid var(--ink);margin-top:2.4rem}
section.feature h2,section.flow h2{margin-top:1rem}
section.feature.conf-low h2::after{content:" ?";color:var(--ochre)}
.kind{font-size:.78rem;color:var(--ink-2);border:1px solid var(--rule);padding:0 .3rem}
article.finding{border-left:4px solid var(--ink-2);padding:.2rem 0 .4rem 1rem;margin:1.2rem 0}
article.claim,article.expect{border-left:4px solid var(--rule);padding:.2rem 0 .4rem 1rem;margin:1.2rem 0}
article.claim h3,article.expect h3{margin-top:.2rem}
.claim.v-contradicted,.expect.r-not_achievable{border-left-color:var(--brick)}
.claim.v-understated,.expect.r-at_risk{border-left-color:var(--ochre)}
.claim.v-partially_true,.expect.r-conditional{border-left-color:var(--ochre)}
.claim.v-confirmed,.expect.r-achievable{border-left-color:var(--moss)}
.claim.v-unverifiable{border-left-color:var(--ink-2);border-left-style:dashed}
.vchip{display:inline-block;font-size:.74rem;font-weight:700;letter-spacing:.03em;padding:.05rem .4rem;border:1px solid currentColor}
.vchip.v-confirmed,.vchip.r-achievable{color:var(--moss)}
.vchip.v-contradicted,.vchip.r-not_achievable{color:var(--brick)}
.vchip.v-partially_true,.vchip.v-understated,.vchip.r-conditional,.vchip.r-at_risk{color:var(--ochre)}
.vchip.v-unverifiable{color:var(--ink-2);border-style:dashed}
.vtally{display:flex;flex-wrap:wrap;gap:.4rem;margin:.2rem 0 1.2rem}
.verdict-line{font-size:1.05rem;border-left:4px solid var(--ochre);padding:.4rem 0 .4rem 1rem;max-width:72ch}

article.finding h3{margin-top:.2rem}
.sev-critical,.sev-high{border-left-color:var(--brick)!important}.sev-medium{border-left-color:var(--ochre)!important}
.sev-low,.sev-info{border-left-color:var(--rule)!important}
article.task,.decision{border-top:1px solid var(--rule);padding-top:.4rem;margin-top:1rem}
dl{display:grid;grid-template-columns:minmax(120px,max-content) 1fr;gap:.5rem 1.2rem;max-width:90ch}
dt{font-weight:700}dd{margin:0}
.empty{border:1.5px dashed var(--ochre);padding:.8rem 1rem;color:var(--ink);max-width:72ch}
.warn-note{margin-top:3rem;border-top:1px solid var(--rule);padding-top:1rem;font-size:.88rem;color:var(--brick)}
.detected{font-size:.92rem}
@media (max-width:900px){body{grid-template-columns:1fr}.atlas-index{position:static;height:auto;border-right:0;border-bottom:1.5px solid var(--ink)}
.atlas-index nav{flex-direction:row;flex-wrap:wrap;gap:0 1rem;border-top:0}.atlas-index nav a{border:0}.legend{display:none}}
@media print{.atlas-index,.plate-actions{display:none}body{display:block}figure.plate{break-inside:avoid}}
"""

MERMAID_JS = r"""<script type="module">
const T = __T__;
const plates = [...document.querySelectorAll('figure.plate')];
const escapeHtml = s => s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let mermaid = null;
try {
  mermaid = (await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs')).default;
  mermaid.initialize({startOnLoad:false, securityLevel:'strict', theme:'base',
    flowchart:{curve:'basis', htmlLabels:true, padding:12}, sequence:{mirrorActors:false, messageAlign:'left'},
    themeVariables:{fontFamily:'Archivo, system-ui, sans-serif', fontSize:'14px',
      primaryColor:'#FBFCFA', primaryTextColor:'#17232C', primaryBorderColor:'#17232C', lineColor:'#4A5A60',
      secondaryColor:'#E3EAE2', tertiaryColor:'#F3F5F0', clusterBkg:'#F3F5F0', clusterBorder:'#8FA08F',
      edgeLabelBackground:'#FBFCFA', actorBkg:'#FBFCFA', actorBorder:'#17232C', actorTextColor:'#17232C',
      signalColor:'#17232C', signalTextColor:'#17232C', noteBkgColor:'#F3E6C8', noteBorderColor:'#A8701A',
      labelBoxBkgColor:'#FBFCFA', activationBkgColor:'#E3EAE2'}});
} catch (e) { console.warn(e); }
let n = 0;
for (const plate of plates) {
  const src = plate.querySelector('pre.mmd'), out = plate.querySelector('.mmd-out');
  const code = src.textContent;
  if (!mermaid) { out.innerHTML = '<div class="mmd-err">' + escapeHtml(T.offline) + '</div>'; src.hidden = false; continue; }
  const id = 'mmd' + (n++);
  try { const {svg} = await mermaid.render(id, code); out.innerHTML = svg; }
  catch (e) {
    document.getElementById('d' + id)?.remove(); document.getElementById(id)?.remove();
    out.innerHTML = '<div class="mmd-err">' + escapeHtml(T.render_err) + ' ' + escapeHtml(String(e.message || e).slice(0, 300)) + '</div>';
    src.hidden = false;
  }
}
document.addEventListener('click', async ev => {
  const btn = ev.target.closest('button[data-act]'); if (!btn) return;
  const plate = btn.closest('figure.plate');
  if (btn.dataset.act === 'expand') {
    const open = plate.classList.toggle('is-open'); btn.textContent = open ? T.close : T.expand; if (open) btn.focus();
  } else if (btn.dataset.act === 'copy') {
    try { await navigator.clipboard.writeText(plate.querySelector('pre.mmd').textContent);
      const old = btn.textContent; btn.textContent = T.copied; setTimeout(() => btn.textContent = old, 1400); } catch (e) {}
  }
});
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape') return;
  document.querySelectorAll('figure.plate.is-open').forEach(p => { p.classList.remove('is-open');
    const b = p.querySelector('button[data-act=expand]'); if (b) b.textContent = T.expand; });
});
</script>"""


def page_conformance(pg: Page, data: dict):
    t, c = pg.t, data.get("conformance.json")
    if not c:
        pg.add(pg.empty("conformance.json"))
        return
    doc = c.get("document") or {}
    head = (f'<p class="prose"><strong>{esc(doc.get("title", ""))}</strong>'
            + (f' — {esc(doc.get("source"))}' if doc.get("source") else "")
            + (f' ({doc.get("pages")} p.)' if doc.get("pages") else "") + "</p>"
            + f'<p class="muted">{esc(t["c_notsot"])}</p>')
    pg.add(pg.section(t["c_doc"], head))
    pg.add(f'<p class="prose">{esc(c.get("summary", ""))}</p>')
    order = {v: i for i, v in enumerate(VERDICTS)}
    claims = sorted(c.get("claims", []), key=lambda x: order.get(x.get("verdict"), 9))
    tally = {}
    for x in claims:
        tally[x.get("verdict")] = tally.get(x.get("verdict"), 0) + 1
    chips = " ".join(f'<span class="vchip v-{esc(v)}">{esc(t.get("v_" + v, v))} {n}</span>'
                     for v, n in sorted(tally.items(), key=lambda kv: order.get(kv[0], 9)))
    blocks = "".join(
        f'<article class="claim v-{esc(x.get("verdict"))}">'
        f'<h3>{esc(x.get("claim"))}</h3>'
        f'<p class="muted"><span class="vchip v-{esc(x.get("verdict"))}">{esc(t.get("v_" + str(x.get("verdict")), ""))}</span>'
        + (f' · {esc(t["c_docref"])}: {esc(x.get("doc_ref"))}' if x.get("doc_ref") else "")
        + (f' · {esc(t["c_impact"])}: {esc(te(t, x.get("impact", "")))}' if x.get("impact") else "") + "</p>"
        f'<p><strong>{esc(t["c_finding"])}:</strong> {esc(x.get("finding"))}</p>'
        + pg.refs.chips(x.get("refs"), t) + "</article>" for x in claims)
    pg.add(pg.section(t["c_claims"], f'<p class="vtally">{chips}</p>{blocks}'))
    gaps = "".join(f'<li><strong>{esc(g.get("title"))}</strong> {esc(g.get("detail", ""))} '
                   + pg.refs.chips(g.get("refs"), t) + "</li>" for g in c.get("gaps", []))
    if gaps:
        pg.add(pg.section(t["c_gaps"], f'<ul class="plain">{gaps}</ul>'))


def page_feasibility(pg: Page, data: dict):
    t, f = pg.t, data.get("feasibility.json")
    if not f:
        pg.add(pg.empty("feasibility.json"))
        return
    pg.add(f'<p class="prose">{esc(f.get("summary", ""))}</p>')
    if f.get("verdict"):
        pg.add(f'<p class="verdict-line">{esc(f["verdict"])}</p>')
    order = {v: i for i, v in enumerate(RATINGS)}
    items = sorted(f.get("expectations", []), key=lambda x: order.get(x.get("rating"), 9))
    blocks = ""
    for x in items:
        r = str(x.get("rating"))
        blocks += (f'<article class="expect r-{esc(r)}"><h3>{esc(x.get("expectation"))}</h3>'
                   f'<p class="muted"><span class="vchip r-{esc(r)}">{esc(t.get("r_" + r, r))}</span>'
                   + (f' · {esc(t["c_docref"])}: {esc(x.get("doc_ref"))}' if x.get("doc_ref") else "")
                   + (f' · {esc(t["f_effort"])}: {esc(te(t, x.get("effort", "")))}' if x.get("effort") else "") + "</p>"
                   f'<p>{esc(x.get("assessment"))}</p>')
        pre = "".join(f"<li>{esc(p)}</li>" for p in x.get("prerequisites", []))
        if pre:
            blocks += f'<h4>{esc(t["f_prereq"])}</h4><ul class="plain">{pre}</ul>'
        if x.get("risk"):
            blocks += f'<p class="muted"><strong>{esc(t["f_risk"])}:</strong> {esc(x["risk"])}</p>'
        blocks += pg.refs.chips(x.get("evidence_refs"), t) + "</article>"
    pg.add(pg.section(t["f_expect"], blocks))
    steps = "".join(f'<li><strong>{esc(s.get("step"))}</strong> {esc(s.get("why", ""))}</li>'
                    for s in f.get("critical_path", []))
    if steps:
        pg.add(pg.section(t["f_path"], f'<ol class="steps">{steps}</ol>'))
    qs = "".join(f'<li><strong>{esc(q.get("question"))}</strong> {esc(q.get("why_it_matters", ""))}'
                 + (f' <span class="muted">({esc(t["f_who"])}: {esc(q.get("who_decides"))})</span>' if q.get("who_decides") else "")
                 + "</li>" for q in f.get("open_questions", []))
    if qs:
        pg.add(pg.section(t["f_open"], f'<ul class="plain">{qs}</ul>'))



def page_readiness(pg: Page, data: dict):
    t, r = pg.t, data.get("readiness.json")
    if not r:
        pg.add(pg.empty("readiness.json"))
        return
    pg.add(f'<p class="prose">{esc(r.get("summary", ""))}</p>')
    if r.get("verdict"):
        pg.add(f'<p class="verdict-line">{esc(r["verdict"])}</p>')
    plates = "".join(pg.plate(x.get("diagram", ""), x.get("title", ""), False, "assessment")
                     + (f'<p class="muted">{esc(x.get("note"))}</p>' if x.get("note") else "")
                     for x in r.get("diagrams", []))
    if plates:
        pg.add(pg.section(t["rd_diagrams"], plates))
    chip = {"in": "v-confirmed", "later": "v-partially_true", "out": "v-contradicted"}
    scope = "".join(
        f'<li><span class="vchip {chip.get(str(x.get("decision")), "")}">'
        f'{esc(t.get("rd_" + str(x.get("decision")), str(x.get("decision"))))}</span> '
        f'<strong>{esc(x.get("item"))}</strong> {esc(x.get("why", ""))} '
        + pg.refs.chips(x.get("refs"), t) + "</li>" for x in r.get("mvp_scope", []))
    if scope:
        pg.add(pg.section(t["rd_scope"], f'<ul class="plain">{scope}</ul>'))
    blocks = ""
    for x in r.get("decisions", []):
        blocks += (f'<article class="expect"><h3>{esc(x.get("question"))}</h3>'
                   + (f'<p class="muted">{esc(t["rd_impact"])}: {esc(te(t, x.get("impact", "")))}</p>'
                      if x.get("impact") else "")
                   + (f'<p>{esc(x.get("background"))}</p>' if x.get("background") else ""))
        blocks += pg.plate(x.get("diagram", ""), x.get("question", ""), False, "assessment")
        opts = "".join(f'<li><strong>{esc(o.get("option"))}</strong> {esc(o.get("tradeoffs", ""))}</li>'
                       for o in x.get("options", []))
        if opts:
            blocks += f'<h4>{esc(t["rd_options"])}</h4><ul class="plain">{opts}</ul>'
        if x.get("recommendation"):
            blocks += f'<p><strong>{esc(t["rd_reco"])}:</strong> {esc(x["recommendation"])}</p>'
        blocks += pg.refs.chips(x.get("refs"), t) + "</article>"
    if blocks:
        pg.add(pg.section(t["rd_decisions"], blocks))
    work = ""
    for w in r.get("workstreams", []):
        work += (f'<article class="expect"><h3>{esc(w.get("title"))}</h3>'
                 f'<p class="muted">{esc(t["rd_outcome"])}: {esc(w.get("outcome", ""))}'
                 + (f' · {esc(t["f_effort"])}: {esc(te(t, w.get("effort", "")))}' if w.get("effort") else "") + "</p>")
        tasks = "".join(f"<li>{esc(task)}</li>" for task in w.get("tasks", []))
        if tasks:
            work += f'<h4>{esc(t["rd_tasks"])}</h4><ul class="plain">{tasks}</ul>'
        work += pg.refs.chips(w.get("refs"), t) + "</article>"
    if work:
        pg.add(pg.section(t["rd_work"], work))
    steps = "".join(f'<li><strong>{esc(s.get("step"))}</strong> {esc(s.get("why", ""))}</li>'
                    for s in r.get("sequence", []))
    if steps:
        pg.add(pg.section(t["rd_seq"], f'<ol class="steps">{steps}</ol>'))
    qs = "".join(f'<li><strong>{esc(q.get("question"))}</strong> {esc(q.get("why_it_matters", ""))}'
                 + (f' <span class="muted">({esc(t["f_who"])}: {esc(q.get("who_decides"))})</span>'
                    if q.get("who_decides") else "") + "</li>" for q in r.get("open_questions", []))
    if qs:
        pg.add(pg.section(t["rd_open"], f'<ul class="plain">{qs}</ul>'))



# --------------------------------------------------------------------------- main

BUILDERS = {"index": page_index, "architecture": page_architecture, "features": page_features,
            "flows": page_flows, "health": page_health, "onboarding": page_onboarding,
            "conformance": page_conformance, "feasibility": page_feasibility,
            "readiness": page_readiness}


def landing_html(facts: dict, langs: list[str], docs: dict) -> str:
    name = facts["repo"]["name"]
    rows = ""
    for x in langs:
        t = L[x]
        links = " ".join(f'<a href="{x}/{esc(fn)}" download>{esc(label)}</a>' for label, fn in docs.get(x, []))
        rows += (f'<li><a class="big" href="{x}/index.html" hreflang="{x}">{esc(t["lang_name"])}</a>'
                 f'<span class="muted">{esc(t["report_title"])}</span>{f"<span>{links}</span>" if links else ""}</li>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(name)}</title>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,300..800&display=swap" rel="stylesheet">
<style>{CSS}
body{{display:block}}main{{margin:0 auto;max-width:760px}}.hero{{margin:0 0 1rem}}
ul.choose{{list-style:none;padding:0;border-top:1.5px solid var(--ink)}}
ul.choose li{{display:flex;flex-wrap:wrap;gap:.4rem 1.2rem;align-items:baseline;padding:1rem 0;border-bottom:1px solid var(--rule)}}
a.big{{font-weight:800;font-stretch:115%;font-size:1.4rem}}</style></head>
<body><main><header class="hero">{contour_svg(name)}<div class="hero-text"><h1>{esc(name)}</h1></div></header>
<ul class="choose">{rows}</ul></main></body></html>"""


BRANDS_ENV, BRANDS_DEFAULT = "REPO_ANALYZER_BRANDS", "~/.claude/repo-analyzer/brands"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--langs", default=None, help="comma list, e.g. en,tr (default: every language under DIR/data)")
    ap.add_argument("--lang", choices=sorted(L), default="tr", help="language of the legacy single-language layout")
    ap.add_argument("--source-lang", default=None, help="language the agents wrote (default: en if present); "
                                                        "the others are checked as translations of it")
    ap.add_argument("--formats", default="html,docx,pdf", help="any of html,docx,pdf (default: all three)")
    ap.add_argument("--mermaid-js", default=None, help="path or URL of mermaid.min.js used for docx/pdf diagrams "
                                                       "(default: jsDelivr CDN; set a local file for offline use)")
    ap.add_argument("--repo-url", default=None, help="web URL used for source links (defaults to git remote)")
    ap.add_argument("--logo", default=None, help="brand logo PNG shown on the docx/pdf cover and page headers")
    ap.add_argument("--brand", default=None, help="brand folder name under the brands dir; uses its logo.png "
                                                  f"(brands dir: ${BRANDS_ENV} or {BRANDS_DEFAULT})")
    ap.add_argument("--check", action="store_true", help="validate only; exit 1 when issues are found")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    out = args.out.resolve()
    if not (out / "facts.json").exists():
        print(f"error: {out}/facts.json not found — run collect_facts.py first", file=sys.stderr)
        return 2
    langs = [x.strip() for x in args.langs.split(",") if x.strip()] if args.langs else (available_langs(out) or [args.lang])
    bad = [x for x in langs if x not in L]
    if bad:
        print(f"error: unsupported language(s) {bad}; available: {sorted(L)}", file=sys.stderr)
        return 2
    src = args.source_lang or ("en" if "en" in langs else langs[0])
    loaded = {x: load(out, x) for x in langs}
    facts = loaded[langs[0]][0]
    refs = Refs(facts, args.repo_url)
    tag = (lambda x, i: f"[{x}] {i}") if len(langs) > 1 else (lambda x, i: i)
    issues = [tag(x, i) for x in langs for i in validate(facts, loaded[x][1], refs)]
    for x in langs:
        if x != src and src in loaded:
            issues += [tag(x, i) for i in translation_issues(loaded[src][1], loaded[x][1], src, x)]
    if args.check:
        if issues:
            print(f"[check] {len(issues)} issue(s):")
            for i in issues:
                print("  -", i)
            return 1
        print(f"[check] OK ({', '.join(langs)})")
        return 0

    formats = {x.strip() for x in args.formats.split(",") if x.strip()}
    if args.brand and not args.logo:
        brand = Path(os.environ.get(BRANDS_ENV) or BRANDS_DEFAULT).expanduser() / args.brand
        if not (brand / "logo.png").is_file():
            print(f"error: {brand}/logo.png not found", file=sys.stderr)
            return 2
        args.logo = str(brand / "logo.png")
    if args.logo:
        os.environ["REPO_ANALYZER_LOGO"] = str(Path(args.logo).expanduser().resolve())
    docs: dict[str, list[tuple[str, str]]] = {}
    exporter = None
    if formats & {"docx", "pdf"}:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import export_docs as exporter  # noqa: E402
        missing = exporter.missing_deps(formats)
        if missing:
            print(f"[docs] skipped Word/PDF: {missing}")
            exporter = None
    for x in langs:
        dest = out / x
        dest.mkdir(parents=True, exist_ok=True)
        data, t = loaded[x][1], L[x]
        if exporter:
            try:
                files = exporter.export(facts, data, t, x, refs, dest, formats, args.mermaid_js, verbose=args.verbose)
                docs[x] = [("Word", f.name) if f.suffix == ".docx" else ("PDF", f.name) for f in files]
                for f in files:
                    print(f"[docs] wrote {f}")
            except Exception as e:  # keep HTML output even if document export fails
                print(f"[docs] {x}: export failed: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()
        if "html" in formats:
            globals()["ACTIVE"] = PAGES + [p for p in OPTIONAL_PAGES if f"{p}.json" in data]
            for key in ACTIVE:
                refs.missing = []
                pg = Page(key, facts, t, x, refs, langs=langs, downloads=docs.get(x))
                BUILDERS[key](pg, data)
                (dest / f"{key}.html").write_text(pg.html())
            print(f"[render] wrote {len(ACTIVE)} HTML pages to {dest}")
    if "html" in formats:
        (out / "index.html").write_text(landing_html(facts, langs, docs))
        print(f"[render] open {out / 'index.html'}")
    if issues:
        print(f"[render] note: {len(issues)} validation issue(s) remain; run with --check to list them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
