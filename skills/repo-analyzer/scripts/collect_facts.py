#!/usr/bin/env python3
"""
collect_facts.py — deterministic fact collector for repo-analyzer.

Stdlib only, no LLM. Produces:
  <out>/facts.json          full machine-readable facts (renderer + reference checks use it)
  <out>/facts_summary.md    compact, LLM-friendly digest (agents read this first)

Usage:
  python collect_facts.py /path/to/repo --out /path/to/output [--git-days 365] [--dep-depth 2]
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import posixpath
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

try:
    import tomllib  # py3.11+
except ImportError:  # pragma: no cover
    tomllib = None

# --------------------------------------------------------------------------- config

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "out", ".next", ".nuxt", "venv", ".venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", "vendor", ".idea",
    ".vscode", "coverage", ".gradle", ".terraform", ".cache", "bower_components", ".turbo",
    ".svelte-kit", "Pods", ".repo-analysis", "site-packages", ".tox", "htmlcov",
}
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
              "composer.lock", "Gemfile.lock", "go.sum", "uv.lock", "bun.lockb"}
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svg", ".pdf", ".zip", ".gz", ".tar",
    ".tgz", ".7z", ".rar", ".jar", ".war", ".class", ".so", ".dylib", ".dll", ".exe", ".bin", ".o",
    ".a", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".wav", ".ogg",
    ".psd", ".sqlite", ".db", ".pyc", ".pkl", ".npy", ".parquet", ".onnx", ".pt", ".h5", ".map",
}
LANG_BY_EXT = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala",
    ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".h": "C/C++ header", ".c": "C", ".swift": "Swift", ".m": "Objective-C", ".dart": "Dart",
    ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".clj": "Clojure", ".hs": "Haskell",
    ".lua": "Lua", ".r": "R", ".jl": "Julia", ".sql": "SQL", ".sh": "Shell", ".bash": "Shell",
    ".zsh": "Shell", ".ps1": "PowerShell", ".vue": "Vue", ".svelte": "Svelte", ".html": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".less": "LESS", ".md": "Markdown", ".mdx": "Markdown",
    ".rst": "reStructuredText", ".yml": "YAML", ".yaml": "YAML", ".json": "JSON", ".toml": "TOML",
    ".xml": "XML", ".proto": "Protobuf", ".graphql": "GraphQL", ".gql": "GraphQL", ".tf": "Terraform",
    ".hcl": "HCL", ".prisma": "Prisma", ".sol": "Solidity", ".zig": "Zig", ".groovy": "Groovy",
    ".gradle": "Gradle", ".fs": "F#", ".ml": "OCaml", ".nim": "Nim", ".txt": "Text", ".ini": "INI", ".cfg": "INI",
}
NON_CODE_LANGS = {"Markdown", "reStructuredText", "JSON", "YAML", "TOML", "XML", "Text", "INI"}

TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|e2e)/|_test\.(go|py|rb|exs?)$|\.(test|spec)\.[jt]sx?$|"
    r"(^|/)test_[^/]+\.py$|Tests?\.(java|kt|cs)$|_spec\.rb$"
)
MARKER_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_style_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("possible_hardcoded_secret", re.compile(
        r"(?i)\b(api[_-]?key|secret[_-]?key|client[_-]?secret|password|passwd|auth[_-]?token)\b"
        r"\s*[:=]\s*['\"][^'\"\s]{12,}['\"]")),
]

# dependency name -> (label, category). Prefix rules below handle versioned/grouped names.
FRAMEWORKS = {
    # JS / TS
    "react": ("React", "frontend"), "next": ("Next.js", "frontend"), "vue": ("Vue", "frontend"),
    "nuxt": ("Nuxt", "frontend"), "@angular/core": ("Angular", "frontend"), "svelte": ("Svelte", "frontend"),
    "@sveltejs/kit": ("SvelteKit", "frontend"), "solid-js": ("Solid", "frontend"),
    "tailwindcss": ("Tailwind CSS", "frontend"), "vite": ("Vite", "build"), "webpack": ("webpack", "build"),
    "express": ("Express", "backend"), "fastify": ("Fastify", "backend"), "@nestjs/core": ("NestJS", "backend"),
    "koa": ("Koa", "backend"), "hono": ("Hono", "backend"), "@trpc/server": ("tRPC", "api"),
    "graphql": ("GraphQL", "api"), "@apollo/server": ("Apollo Server", "api"),
    "prisma": ("Prisma", "database"), "@prisma/client": ("Prisma", "database"), "typeorm": ("TypeORM", "database"),
    "sequelize": ("Sequelize", "database"), "mongoose": ("Mongoose", "database"),
    "drizzle-orm": ("Drizzle", "database"), "knex": ("Knex", "database"), "pg": ("PostgreSQL driver", "database"),
    "redis": ("Redis", "cache"), "ioredis": ("Redis", "cache"), "bullmq": ("BullMQ", "queue"),
    "kafkajs": ("Kafka", "queue"), "amqplib": ("RabbitMQ", "queue"), "socket.io": ("Socket.IO", "realtime"),
    "jest": ("Jest", "testing"), "vitest": ("Vitest", "testing"), "mocha": ("Mocha", "testing"),
    "@playwright/test": ("Playwright", "testing"), "cypress": ("Cypress", "testing"),
    "electron": ("Electron", "desktop"), "react-native": ("React Native", "mobile"), "expo": ("Expo", "mobile"),
    "stripe": ("Stripe", "payments"), "openai": ("OpenAI SDK", "ai"), "@anthropic-ai/sdk": ("Anthropic SDK", "ai"),
    "langchain": ("LangChain", "ai"), "passport": ("Passport", "auth"), "next-auth": ("NextAuth", "auth"),
    "jsonwebtoken": ("JWT", "auth"), "zod": ("Zod", "validation"),
    # Python
    "django": ("Django", "backend"), "flask": ("Flask", "backend"), "fastapi": ("FastAPI", "backend"),
    "starlette": ("Starlette", "backend"), "aiohttp": ("aiohttp", "backend"), "tornado": ("Tornado", "backend"),
    "sqlalchemy": ("SQLAlchemy", "database"), "alembic": ("Alembic", "database"),
    "psycopg2": ("PostgreSQL driver", "database"), "psycopg2-binary": ("PostgreSQL driver", "database"),
    "psycopg": ("PostgreSQL driver", "database"), "asyncpg": ("PostgreSQL driver", "database"),
    "pymongo": ("MongoDB", "database"), "celery": ("Celery", "queue"), "pydantic": ("Pydantic", "validation"),
    "pytest": ("pytest", "testing"), "pandas": ("pandas", "data"), "numpy": ("NumPy", "data"),
    "torch": ("PyTorch", "ai"), "tensorflow": ("TensorFlow", "ai"), "scikit-learn": ("scikit-learn", "ai"),
    "transformers": ("Hugging Face Transformers", "ai"), "anthropic": ("Anthropic SDK", "ai"),
    "boto3": ("AWS SDK", "cloud"), "grpcio": ("gRPC", "api"), "strawberry-graphql": ("Strawberry GraphQL", "api"),
    "graphene": ("Graphene", "api"), "streamlit": ("Streamlit", "frontend"), "gradio": ("Gradio", "frontend"),
    "uvicorn": ("Uvicorn", "server"), "gunicorn": ("Gunicorn", "server"), "apache-airflow": ("Airflow", "data"),
    "dbt-core": ("dbt", "data"), "scrapy": ("Scrapy", "data"), "click": ("Click CLI", "cli"), "typer": ("Typer CLI", "cli"),
    "kafka-python": ("Kafka", "queue"), "confluent-kafka": ("Kafka", "queue"),
    # Rust
    "actix-web": ("Actix Web", "backend"), "axum": ("Axum", "backend"), "rocket": ("Rocket", "backend"),
    "tokio": ("Tokio", "runtime"), "diesel": ("Diesel", "database"), "sqlx": ("SQLx", "database"),
    "clap": ("clap CLI", "cli"), "serde": ("Serde", "serialization"),
    # Ruby / PHP
    "rails": ("Rails", "backend"), "sidekiq": ("Sidekiq", "queue"), "rspec": ("RSpec", "testing"),
    "laravel/framework": ("Laravel", "backend"), "symfony/framework-bundle": ("Symfony", "backend"),
}
PREFIX_FRAMEWORKS = [
    ("github.com/gin-gonic/gin", ("Gin", "backend")), ("github.com/labstack/echo", ("Echo", "backend")),
    ("github.com/gofiber/fiber", ("Fiber", "backend")), ("github.com/go-chi/chi", ("chi", "backend")),
    ("gorm.io/gorm", ("GORM", "database")), ("google.golang.org/grpc", ("gRPC", "api")),
    ("github.com/spf13/cobra", ("Cobra CLI", "cli")), ("github.com/jackc/pgx", ("PostgreSQL driver", "database")),
    ("github.com/redis/go-redis", ("Redis", "cache")), ("spring-boot", ("Spring Boot", "backend")),
    ("org.springframework.boot", ("Spring Boot", "backend")), ("hibernate", ("Hibernate", "database")),
    ("junit", ("JUnit", "testing")), ("@aws-sdk/", ("AWS SDK", "cloud")), ("@google-cloud/", ("Google Cloud SDK", "cloud")),
    ("@azure/", ("Azure SDK", "cloud")), ("@nestjs/", ("NestJS", "backend")), ("@remix-run/", ("Remix", "frontend")),
]

ENTRY_NAMES = {
    "main.py", "__main__.py", "manage.py", "app.py", "wsgi.py", "asgi.py", "server.py", "cli.py",
    "server.js", "server.ts", "app.js", "app.ts", "index.js", "index.ts", "main.js", "main.ts", "main.tsx",
    "main.go", "main.rs", "lib.rs", "Program.cs", "main.dart", "index.php", "config.ru",
}

# --------------------------------------------------------------------------- helpers


def run(cmd: list[str], cwd: Path, timeout: int = 180) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, errors="replace")
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def read_text(path: Path, limit: int = 1_000_000) -> str | None:
    try:
        with open(path, "rb") as f:
            data = f.read(limit + 1)
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data[:limit].decode("utf-8", errors="replace")


def norm(p: str) -> str:
    p = posixpath.normpath(p.replace(os.sep, "/"))
    return "" if p == "." else p


def dir_key(path: str, depth: int) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth]) if parts else "(root)"


# --------------------------------------------------------------------------- file listing


def list_files(root: Path, is_git: bool, exclude: set[str]) -> list[str]:
    files: list[str] = []
    if is_git:
        out = run(["git", "ls-files", "-co", "--exclude-standard", "-z"], root)
        for p in out.split("\0"):
            if not p:
                continue
            parts = p.split("/")
            if any(x in SKIP_DIRS for x in parts[:-1]) or any(p.startswith(e) for e in exclude):
                continue
            if (root / p).is_file() and not (root / p).is_symlink():
                files.append(p)
    if not files:
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                           and not any(norm(posixpath.join(rel_dir, d)).startswith(e) for e in exclude)]
            for fn in filenames:
                full = Path(dirpath) / fn
                if full.is_symlink():
                    continue
                files.append(norm(posixpath.join(rel_dir.replace(os.sep, "/"), fn)))
    return sorted(set(files))


# --------------------------------------------------------------------------- manifests


def parse_manifests(root: Path, files: list[str]) -> tuple[list[dict], dict]:
    manifests, frameworks = [], {}

    def add_deps(names, source):
        for raw in names:
            name = raw.strip().lower() if not raw.startswith(("github.com", "gorm.io", "google.golang.org")) else raw.strip()
            hit = FRAMEWORKS.get(name)
            if not hit:
                for prefix, val in PREFIX_FRAMEWORKS:
                    if name.startswith(prefix) or prefix in name:
                        hit = val
                        break
            if hit:
                frameworks.setdefault(hit[0], {"category": hit[1], "sources": []})
                if source not in frameworks[hit[0]]["sources"]:
                    frameworks[hit[0]]["sources"].append(source)

    for rel in files:
        base = rel.rsplit("/", 1)[-1]
        full = root / rel
        entry = None
        try:
            if base == "package.json":
                data = json.loads(read_text(full) or "{}")
                deps = list((data.get("dependencies") or {}).keys())
                dev = list((data.get("devDependencies") or {}).keys())
                entry = {"type": "npm", "path": rel, "name": data.get("name"), "scripts": data.get("scripts") or {},
                         "dependencies": deps[:80], "devDependencies": dev[:80], "main": data.get("main"),
                         "bin": data.get("bin"), "workspaces": data.get("workspaces")}
                add_deps(deps + dev, rel)
            elif base == "pyproject.toml" and tomllib:
                data = tomllib.loads(read_text(full) or "")
                proj = data.get("project", {})
                deps = [re.split(r"[<>=!~;\[ ]", d)[0] for d in proj.get("dependencies", [])]
                poetry = data.get("tool", {}).get("poetry", {})
                deps += [d for d in poetry.get("dependencies", {}) if d != "python"]
                entry = {"type": "python", "path": rel, "name": proj.get("name") or poetry.get("name"),
                         "dependencies": deps[:80], "scripts": proj.get("scripts") or poetry.get("scripts") or {}}
                add_deps(deps, rel)
            elif re.match(r"requirements.*\.txt$", base):
                deps = []
                for line in (read_text(full) or "").splitlines():
                    line = line.strip()
                    if line and not line.startswith(("#", "-")):
                        deps.append(re.split(r"[<>=!~;\[ ]", line)[0])
                entry = {"type": "python", "path": rel, "dependencies": deps[:80]}
                add_deps(deps, rel)
            elif base == "go.mod":
                text = read_text(full) or ""
                mod = re.search(r"^module\s+(\S+)", text, re.M)
                deps = re.findall(r"^\s*([a-z0-9.\-]+\.[a-z]+/[^\s]+)\s+v", text, re.M)
                entry = {"type": "go", "path": rel, "name": mod.group(1) if mod else None, "dependencies": deps[:80]}
                add_deps(deps, rel)
            elif base == "Cargo.toml" and tomllib:
                data = tomllib.loads(read_text(full) or "")
                deps = list(data.get("dependencies", {}).keys())
                entry = {"type": "cargo", "path": rel, "name": data.get("package", {}).get("name"),
                         "dependencies": deps[:80], "workspace": data.get("workspace", {}).get("members")}
                add_deps(deps, rel)
            elif base == "pom.xml":
                text = read_text(full) or ""
                deps = re.findall(r"<artifactId>([^<]+)</artifactId>", text)
                entry = {"type": "maven", "path": rel, "dependencies": deps[:80]}
                add_deps(deps, rel)
            elif base in ("build.gradle", "build.gradle.kts"):
                text = read_text(full) or ""
                deps = [m.split(":")[1] if m.count(":") >= 1 else m for m in re.findall(
                    r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*\(?\s*['\"]([^'\"]+)['\"]", text)]
                entry = {"type": "gradle", "path": rel, "dependencies": deps[:80]}
                add_deps(deps, rel)
            elif base == "Gemfile":
                deps = re.findall(r"^\s*gem\s+['\"]([\w\-]+)['\"]", read_text(full) or "", re.M)
                entry = {"type": "ruby", "path": rel, "dependencies": deps[:80]}
                add_deps(deps, rel)
            elif base == "composer.json":
                data = json.loads(read_text(full) or "{}")
                deps = list((data.get("require") or {}).keys())
                entry = {"type": "php", "path": rel, "name": data.get("name"), "dependencies": deps[:80]}
                add_deps(deps, rel)
        except Exception as exc:  # malformed manifest shouldn't kill the run
            entry = {"type": "unparsed", "path": rel, "error": str(exc)[:120]}
        if entry:
            manifests.append(entry)
    return manifests[:40], frameworks


def collect_infra(root: Path, files: list[str]) -> dict:
    infra = {"dockerfiles": [], "compose_services": [], "ci": [], "k8s_kinds": Counter(),
             "terraform_resources": Counter(), "makefile_targets": [], "env_keys": [], "openapi_specs": [],
             "prisma_models": [], "migrations": 0}
    for rel in files:
        base = rel.rsplit("/", 1)[-1]
        low = rel.lower()
        if base.startswith("Dockerfile") or base.endswith(".dockerfile"):
            text = read_text(root / rel) or ""
            infra["dockerfiles"].append({
                "path": rel, "from": re.findall(r"^\s*FROM\s+(\S+)", text, re.M | re.I)[:4],
                "expose": re.findall(r"^\s*EXPOSE\s+(.+)$", text, re.M | re.I)[:4],
                "cmd": (re.findall(r"^\s*(?:CMD|ENTRYPOINT)\s+(.+)$", text, re.M | re.I) or [None])[-1]})
        elif re.match(r"(docker-)?compose(\.[\w-]+)?\.ya?ml$", base):
            text = read_text(root / rel) or ""
            m = re.search(r"^services:\s*\n((?:[ \t]+.*\n?|\s*\n)*)", text, re.M)
            if m:
                names = re.findall(r"^  ([A-Za-z0-9_.\-]+):\s*$", m.group(1), re.M)
                infra["compose_services"].append({"path": rel, "services": names})
        elif low.startswith(".github/workflows/") and base.endswith((".yml", ".yaml")):
            text = read_text(root / rel) or ""
            name = re.search(r"^name:\s*(.+)$", text, re.M)
            infra["ci"].append({"path": rel, "system": "GitHub Actions", "name": name.group(1).strip() if name else base})
        elif base in (".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", "bitbucket-pipelines.yml") \
                or low.startswith(".circleci/"):
            infra["ci"].append({"path": rel, "system": base})
        elif base in ("Makefile", "makefile", "justfile"):
            targets = re.findall(r"^([A-Za-z0-9_\-]+):(?!=)", read_text(root / rel) or "", re.M)
            infra["makefile_targets"].append({"path": rel, "targets": targets[:30]})
        elif re.match(r"\.env\.(example|sample|template|dist)$", base) or base == "env.example":
            keys = re.findall(r"^\s*([A-Z][A-Z0-9_]+)\s*=", read_text(root / rel) or "", re.M)
            infra["env_keys"].append({"path": rel, "keys": keys[:60]})  # names only, never values
        elif base.endswith(".tf"):
            for rtype in re.findall(r'^resource\s+"([\w\-]+)"', read_text(root / rel) or "", re.M):
                infra["terraform_resources"][rtype] += 1
        elif base.endswith((".yml", ".yaml")) and ("k8s" in low or "kube" in low or "helm" in low or "deploy" in low):
            for kind in re.findall(r"^kind:\s*(\w+)", read_text(root / rel) or "", re.M):
                infra["k8s_kinds"][kind] += 1
        if base.endswith((".yml", ".yaml", ".json")) and re.search(r"(openapi|swagger)", base, re.I):
            infra["openapi_specs"].append(rel)
        if base.endswith(".prisma"):
            infra["prisma_models"] += re.findall(r"^model\s+(\w+)", read_text(root / rel) or "", re.M)
        if re.search(r"(^|/)(migrations?|alembic/versions|db/migrate)/", rel):
            infra["migrations"] += 1
    infra["k8s_kinds"] = dict(infra["k8s_kinds"])
    infra["terraform_resources"] = dict(infra["terraform_resources"].most_common(30))
    return infra


# --------------------------------------------------------------------------- routes

ROUTE_RULES = [
    # (framework, regex, method_group, path_group, file_filter, requires_leading_slash)
    ("express/koa/hono", re.compile(r"""\b(?:app|router|server|api|r)\.(get|post|put|patch|delete|all)\(\s*['"`]([^'"`]+)['"`]"""), 1, 2, r"\.[cm]?[jt]sx?$", True),
    ("fastapi/flask", re.compile(r"""@\w+(?:\.\w+)*\.(get|post|put|patch|delete|route|websocket|api_route)\(\s*['"]([^'"]*)['"]"""), 1, 2, r"\.py$", False),
    ("django", re.compile(r"""\b(?:re_)?path\(\s*r?['"]([^'"]*)['"]"""), None, 1, r"(^|/)urls\.py$", False),
    ("spring", re.compile(r"""@(Get|Post|Put|Patch|Delete|Request)Mapping\(\s*(?:value\s*=\s*|path\s*=\s*)?\{?\s*"([^"]*)\""""), 1, 2, r"\.(java|kt)$", False),
    ("nestjs", re.compile(r"""@(Get|Post|Put|Patch|Delete|Controller)\(\s*['"]([^'"]*)['"]"""), 1, 2, r"\.ts$", False),
    ("go-http", re.compile(r"""\.(GET|POST|PUT|PATCH|DELETE|Handle|HandleFunc|Get|Post|Put|Delete|Patch)\(\s*"(/[^"]*)\""""), 1, 2, r"\.go$", True),
    ("rails", re.compile(r"""^\s*(get|post|put|patch|delete|resources|resource)\s+['":]([\w/:\-]+)""", re.M), 1, 2, r"(^|/)routes\.rb$", False),
]


def file_based_routes(rel: str, fw: set[str]) -> dict | None:
    m = re.match(r"^(?:src/)?app/(.*?)(?:/)?(page|route)\.(tsx|jsx|ts|js)$", rel)
    if m and "Next.js" in fw:
        route = "/" + re.sub(r"\([^)]*\)/?", "", m.group(1)).strip("/")
        return {"framework": "next-app", "method": "PAGE" if m.group(2) == "page" else "API", "path": route, "ref": rel}
    m = re.match(r"^(?:src/)?pages/(.*)\.(tsx|jsx|ts|js|vue)$", rel)
    if m and fw & {"Next.js", "Nuxt"} and not m.group(1).split("/")[-1].startswith("_"):
        route = "/" + re.sub(r"(^|/)index$", "", m.group(1))
        return {"framework": "pages-router", "method": "API" if route.startswith("/api") else "PAGE", "path": route, "ref": rel}
    m = re.match(r"^(?:src/)?routes/(.*?)/?\+(page|server)\.(svelte|ts|js)$", rel)
    if m and "SvelteKit" in fw:
        return {"framework": "sveltekit", "method": "PAGE" if m.group(2) == "page" else "API", "path": "/" + m.group(1), "ref": rel}
    return None


# --------------------------------------------------------------------------- import graph

JS_IMPORT_RE = re.compile(
    r"""(?:import|export)\s[^'";]*?from\s*['"]([^'"]+)['"]|import\(\s*['"]([^'"]+)['"]\s*\)|"""
    r"""require\(\s*['"]([^'"]+)['"]\s*\)|^\s*import\s+['"]([^'"]+)['"]""", re.M)
JS_EXTS = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
           "/index.ts", "/index.tsx", "/index.js", "/index.jsx"]
GO_IMPORT_RE = re.compile(r'^\s*(?:import\s+)?(?:\w+\s+)?"([^"]+)"', re.M)
JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;?", re.M)


class ImportGraph:
    def __init__(self, files: list[str], go_module: str | None):
        self.fileset = set(files)
        self.go_module = go_module
        self.py_index: dict[str, str] = {}
        for f in files:
            if f.endswith(".py"):
                mod = f[:-3].replace("/", ".")
                if mod.endswith(".__init__"):
                    mod = mod[: -len(".__init__")]
                for prefix in ("", "src.", "lib.", "app."):
                    if mod.startswith(prefix) and prefix:
                        self.py_index.setdefault(mod[len(prefix):], f)
                self.py_index.setdefault(mod, f)
        self.jvm_index: dict[str, list[str]] = defaultdict(list)
        for f in files:
            if f.endswith((".java", ".kt")):
                self.jvm_index[f.rsplit("/", 1)[-1].rsplit(".", 1)[0]].append(f)
        self.go_dirs: dict[str, str] = {}
        for f in files:
            if f.endswith(".go") and not f.endswith("_test.go"):
                self.go_dirs.setdefault(f.rsplit("/", 1)[0] if "/" in f else "", f)
        self.edges: Counter = Counter()

    def _py(self, rel: str, text: str):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return
        pkg = rel[:-3].replace("/", ".").split(".")[:-1]
        if rel.endswith("__init__.py"):
            pkg = rel[:-3].replace("/", ".").split(".")[:-1]
        targets: set[str] = set()

        def resolve(name: str) -> str | None:
            parts = name.split(".")
            while parts:
                hit = self.py_index.get(".".join(parts))
                if hit:
                    return hit
                parts.pop()
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    hit = resolve(a.name)
                    if hit:
                        targets.add(hit)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                    base = ".".join(parent + ([base] if base else []))
                for a in node.names:
                    full = f"{base}.{a.name}" if base else a.name
                    hit = self.py_index.get(full) or resolve(base) if base else self.py_index.get(full)
                    if hit:
                        targets.add(hit)
        for t in targets - {rel}:
            self.edges[(rel, t)] += 1

    def _js(self, rel: str, text: str):
        here = rel.rsplit("/", 1)[0] if "/" in rel else ""
        for m in JS_IMPORT_RE.finditer(text):
            spec = next(g for g in m.groups() if g)
            if spec.startswith("."):
                cands = [norm(posixpath.join(here, spec))]
            elif spec.startswith(("@/", "~/")):
                cands = [norm("src/" + spec[2:]), norm(spec[2:])]
            else:
                continue
            for base in cands:
                hit = next((base + e for e in JS_EXTS if (base + e) in self.fileset), None)
                if hit and hit != rel:
                    self.edges[(rel, hit)] += 1
                    break

    def _go(self, rel: str, text: str):
        if not self.go_module:
            return
        block = re.findall(r"import\s*\((.*?)\)", text, re.S)
        specs = GO_IMPORT_RE.findall("\n".join(block)) + re.findall(r'^import\s+(?:\w+\s+)?"([^"]+)"', text, re.M)
        for spec in specs:
            if spec.startswith(self.go_module):
                d = spec[len(self.go_module):].strip("/")
                target = self.go_dirs.get(d)
                if target and target.rsplit("/", 1)[0] != rel.rsplit("/", 1)[0]:
                    self.edges[(rel, target)] += 1

    def _jvm(self, rel: str, text: str):
        for spec in JAVA_IMPORT_RE.findall(text):
            parts = spec.split(".")
            for cls_idx in range(len(parts) - 1, 0, -1):
                suffix = "/".join(parts[: cls_idx + 1])
                for cand in self.jvm_index.get(parts[cls_idx], []):
                    if cand.rsplit(".", 1)[0].endswith(suffix) and cand != rel:
                        self.edges[(rel, cand)] += 1
                        break
                else:
                    continue
                break

    def add(self, rel: str, text: str):
        if rel.endswith(".py"):
            self._py(rel, text)
        elif rel.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte")):
            self._js(rel, text)
        elif rel.endswith(".go"):
            self._go(rel, text)
        elif rel.endswith((".java", ".kt")):
            self._jvm(rel, text)


# --------------------------------------------------------------------------- git forensics


def git_forensics(root: Path, days: int, existing: set[str], code_files: set[str], depth: int) -> dict:
    head = run(["git", "rev-parse", "HEAD"], root).strip()
    if not head:
        return {"available": False}
    info = {
        "available": True, "head": head, "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root).strip(),
        "total_commits": int((run(["git", "rev-list", "--count", "HEAD"], root).strip() or "0")),
        "first_commit": run(["git", "log", "-1", "--format=%aI",
                             (run(["git", "rev-list", "--max-parents=0", "HEAD"], root).split() or ["HEAD"])[-1]], root).strip(),
        "last_commit": run(["git", "log", "-1", "--format=%aI"], root).strip(),
        "window_days": days,
    }
    remote = run(["git", "remote", "get-url", "origin"], root).strip()
    m = re.match(r"git@([^:]+):(.+?)(\.git)?$", remote)
    info["remote_web"] = f"https://{m.group(1)}/{m.group(2)}" if m else re.sub(r"\.git$", "", remote) if remote.startswith("http") else ""

    log = run(["git", "log", f"--since={days}.days", "--no-merges", "--numstat",
               "--format=__C__%H\t%an\t%at\t%s", "--max-count=6000"], root, timeout=300)
    commits, cur = [], None
    for line in log.splitlines():
        if line.startswith("__C__"):
            parts = line[5:].split("\t", 3)
            cur = {"author": parts[1] if len(parts) > 1 else "?", "ts": int(parts[2]) if len(parts) > 2 else 0,
                   "msg": parts[3] if len(parts) > 3 else "", "files": []}
            commits.append(cur)
        elif line.strip() and cur is not None:
            parts = line.split("\t")
            if len(parts) == 3:
                path = parts[2]
                path = re.sub(r"\{([^{}]*) => ([^{}]*)\}", r"\2", path)
                path = path.split(" => ")[-1].replace("//", "/")
                add = int(parts[0]) if parts[0].isdigit() else 0
                dele = int(parts[1]) if parts[1].isdigit() else 0
                cur["files"].append((path, add, dele))

    per_file = defaultdict(lambda: {"commits": 0, "added": 0, "deleted": 0, "authors": Counter(), "fixes": 0})
    dir_authors: dict[str, Counter] = defaultdict(Counter)
    authors = Counter()
    fix_re = re.compile(r"(?i)\b(fix|bug|hotfix|patch|regression|crash)\b")
    pair_counts: Counter = Counter()
    for c in commits:
        authors[c["author"]] += 1
        is_fix = bool(fix_re.search(c["msg"]))
        touched = []
        for path, add, dele in c["files"]:
            if path not in existing:
                continue
            pf = per_file[path]
            pf["commits"] += 1
            pf["added"] += add
            pf["deleted"] += dele
            pf["authors"][c["author"]] += 1
            pf["fixes"] += int(is_fix)
            dir_authors[dir_key(path, 1)][c["author"]] += 1
            if path in code_files:
                touched.append(path)
        if 2 <= len(touched) <= 30:
            for a, b in combinations(sorted(set(touched)), 2):
                pair_counts[(a, b)] += 1

    info["window_commits"] = len(commits)
    info["authors_in_window"] = len(authors)
    info["top_authors"] = [{"name": n, "commits": k} for n, k in authors.most_common(10)]

    def bus_factor(counter: Counter) -> tuple[int, str, float]:
        total = sum(counter.values()) or 1
        acc, k = 0, 0
        for _, n in counter.most_common():
            acc += n
            k += 1
            if acc / total >= 0.5:
                break
        top, top_n = counter.most_common(1)[0]
        return k, top, round(top_n / total, 2)

    info["bus_factor"] = []
    for d, counter in sorted(dir_authors.items(), key=lambda kv: -sum(kv[1].values()))[:20]:
        k, top, share = bus_factor(counter)
        info["bus_factor"].append({"dir": d, "bus_factor": k, "top_author": top, "top_share": share,
                                   "authors": len(counter), "commits": sum(counter.values())})
    if authors:
        k, top, share = bus_factor(authors)
        info["bus_factor_overall"] = {"bus_factor": k, "top_author": top, "top_share": share}

    info["_per_file"] = per_file  # consumed by hotspot calc, removed before dump
    info["coupling"] = []
    for (a, b), n in pair_counts.most_common(400):
        if n < 3:
            break
        degree = n / max(1, min(per_file[a]["commits"], per_file[b]["commits"]))
        info["coupling"].append({"a": a, "b": b, "shared_commits": n, "degree": round(degree, 2),
                                 "cross_dir": dir_key(a, depth) != dir_key(b, depth)})
    info["coupling"].sort(key=lambda x: -(x["shared_commits"] * x["degree"]))
    info["coupling"] = info["coupling"][:25]

    ts_out = run(["git", "log", "--since=730.days", "--format=%at"], root)
    months = Counter(time.strftime("%Y-%m", time.gmtime(int(t))) for t in ts_out.split() if t.isdigit())
    info["monthly_commits"] = [{"month": k, "commits": months[k]} for k in sorted(months)]
    return info


# --------------------------------------------------------------------------- main scan


def collect(root: Path, out: Path, git_days: int, dep_depth: int) -> dict:
    t0 = time.time()
    is_git = (root / ".git").exists() and bool(run(["git", "rev-parse", "--git-dir"], root).strip())
    exclude = set()
    try:
        exclude.add(norm(os.path.relpath(out, root)) + "/")
    except ValueError:
        pass
    all_files = list_files(root, is_git, {e for e in exclude if not e.startswith("..")})

    files, lang_stats = [], defaultdict(lambda: {"files": 0, "lines": 0})
    markers, secrets, routes = [], [], []
    marker_per_file = Counter()
    manifests, frameworks = parse_manifests(root, all_files)
    go_module = next((m.get("name") for m in manifests if m.get("type") == "go" and m.get("name")), None)
    graph = ImportGraph(all_files, go_module)
    fw_names = set(frameworks)
    entry_points = []

    for rel in all_files:
        base = rel.rsplit("/", 1)[-1]
        ext = ("." + base.rsplit(".", 1)[-1].lower()) if "." in base else ""
        if ext in BINARY_EXT or base in SKIP_FILES:
            continue
        full = root / rel
        try:
            size = full.stat().st_size
        except OSError:
            continue
        text = read_text(full) if size <= 1_500_000 else None
        if text is None:
            continue
        lang = LANG_BY_EXT.get(ext) or ("Dockerfile" if base.startswith("Dockerfile") else "Other")
        lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        is_test = bool(TEST_RE.search(rel))
        is_code = lang not in NON_CODE_LANGS and lang != "Other"
        files.append({"path": rel, "lang": lang, "lines": lines, "bytes": size, "test": is_test, "code": is_code})
        lang_stats[lang]["files"] += 1
        lang_stats[lang]["lines"] += lines

        if base in ENTRY_NAMES or re.search(r"(^|/)cmd/[^/]+/main\.go$|Application\.(java|kt)$", rel):
            entry_points.append(rel)

        if is_code:
            graph.add(rel, text)
            for i, line in enumerate(text.splitlines(), 1):
                if MARKER_RE.search(line):
                    marker_per_file[rel] += 1
                    if len(markers) < 60:
                        markers.append({"ref": f"{rel}:{i}", "text": line.strip()[:140]})
        if base not in SKIP_FILES and not is_test:
            for i, line in enumerate(text.splitlines(), 1):
                if len(line) > 2000:
                    continue
                for kind, rx in SECRET_PATTERNS:
                    if rx.search(line):
                        secrets.append({"ref": f"{rel}:{i}", "kind": kind})  # never store the value
                        break
        for fw, rx, mg, pg, ffilter, need_slash in ROUTE_RULES:
            if not re.search(ffilter, rel):
                continue
            for m in rx.finditer(text):
                path = m.group(pg)
                if need_slash and not path.startswith("/"):
                    continue
                line_no = text.count("\n", 0, m.start()) + 1
                routes.append({"framework": fw, "method": (m.group(mg) if mg else "ANY").upper(),
                               "path": path or "/", "ref": f"{rel}:{line_no}"})
        fb = file_based_routes(rel, fw_names)
        if fb:
            routes.append(fb)

    fileset = {f["path"] for f in files}
    code_files = {f["path"] for f in files if f["code"]}
    line_map = {f["path"]: f["lines"] for f in files}

    # dependency graph (file + directory level)
    file_edges = [{"from": a, "to": b, "weight": w} for (a, b), w in graph.edges.most_common(600)]
    dir_edges = Counter()
    for (a, b), w in graph.edges.items():
        da, db = dir_key(a, dep_depth), dir_key(b, dep_depth)
        if da != db:
            dir_edges[(da, db)] += w
    fan_in = Counter(b for (_, b) in graph.edges)
    fan_out = Counter(a for (a, _) in graph.edges)

    git = git_forensics(root, git_days, fileset, code_files, dep_depth) if is_git else {"available": False}
    hotspots = []
    if git.get("available"):
        pf = git.pop("_per_file")
        cand = [(p, d) for p, d in pf.items() if p in code_files and not TEST_RE.search(p)]
        if cand:
            max_c = max(d["commits"] for _, d in cand) or 1
            max_l = max(math.log1p(line_map.get(p, 0)) for p, _ in cand) or 1
            for p, d in cand:
                score = (d["commits"] / max_c) * (math.log1p(line_map.get(p, 0)) / max_l)
                hotspots.append({"path": p, "commits": d["commits"], "lines": line_map.get(p, 0),
                                 "churn": d["added"] + d["deleted"], "fix_commits": d["fixes"],
                                 "authors": len(d["authors"]), "score": round(score, 3)})
            hotspots = [h for h in hotspots if h["score"] > 0]
            hotspots.sort(key=lambda h: -h["score"])
            hotspots = hotspots[:30]

    src_lines = sum(f["lines"] for f in files if f["code"] and not f["test"])
    test_lines = sum(f["lines"] for f in files if f["code"] and f["test"])
    readme = next((f for f in all_files if re.match(r"readme(\.\w+)?$", f, re.I)), None)

    facts = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo": {"name": root.name, "path": str(root), "is_git": is_git},
        "totals": {"files": len(files), "lines": sum(f["lines"] for f in files), "code_files": len(code_files),
                   "source_lines": src_lines, "test_lines": test_lines,
                   "test_ratio": round(test_lines / src_lines, 2) if src_lines else 0,
                   "test_files": sum(1 for f in files if f["test"])},
        "languages": sorted(({"lang": k, **v} for k, v in lang_stats.items()), key=lambda x: -x["lines"]),
        "frameworks": [{"name": k, **v} for k, v in sorted(frameworks.items(), key=lambda kv: kv[1]["category"])],
        "manifests": manifests,
        "infra": collect_infra(root, all_files),
        "entry_points": entry_points[:40],
        "routes": routes[:400],
        "readme": readme,
        "dependency_graph": {
            "depth": dep_depth, "file_edges": file_edges,
            "dir_edges": [{"from": a, "to": b, "weight": w} for (a, b), w in dir_edges.most_common(80)],
            "most_depended_on": [{"path": p, "fan_in": n} for p, n in fan_in.most_common(15)],
            "most_dependent": [{"path": p, "fan_out": n} for p, n in fan_out.most_common(15)],
        },
        "git": git,
        "hotspots": hotspots,
        "markers": {"total": sum(marker_per_file.values()),
                    "top_files": [{"path": p, "count": n} for p, n in marker_per_file.most_common(15)],
                    "samples": markers},
        "secrets_candidates": secrets[:50],
        "largest_files": sorted(({"path": f["path"], "lines": f["lines"]} for f in files if f["code"]),
                                key=lambda x: -x["lines"])[:15],
        "files": [{"path": f["path"], "lines": f["lines"], "lang": f["lang"], "test": f["test"]} for f in files],
        "elapsed_sec": round(time.time() - t0, 2),
    }
    return facts


# --------------------------------------------------------------------------- summary for LLMs


def tree_lines(paths: list[str], max_depth: int = 3, max_lines: int = 160) -> list[str]:
    counts: Counter = Counter()
    for p in paths:
        parts = p.split("/")
        for d in range(1, min(len(parts), max_depth + 1)):
            counts["/".join(parts[:d]) + "/"] += 1
        if len(parts) <= max_depth:
            counts[p] = 0
    lines = []
    for key in sorted(counts):
        depth = key.rstrip("/").count("/")
        name = key.rstrip("/").rsplit("/", 1)[-1] + ("/" if key.endswith("/") else "")
        suffix = f"  ({counts[key]} files)" if key.endswith("/") else ""
        lines.append("  " * depth + name + suffix)
        if len(lines) >= max_lines:
            lines.append("  … (truncated)")
            break
    return lines


def summary_md(facts: dict, root: Path) -> str:
    t, g = facts["totals"], facts["git"]
    out = [f"# Facts summary: {facts['repo']['name']}", "",
           "Deterministic facts gathered by collect_facts.py. Treat as ground truth; verify details in code.", "",
           f"- Files: {t['files']} (code: {t['code_files']}), lines: {t['lines']}, "
           f"source lines: {t['source_lines']}, test lines: {t['test_lines']} (ratio {t['test_ratio']})"]
    if g.get("available"):
        out.append(f"- Git: HEAD {g['head'][:10]} on {g['branch']}, {g['total_commits']} commits, "
                   f"first {g['first_commit'][:10]}, last {g['last_commit'][:10]}, "
                   f"{g['authors_in_window']} authors in last {g['window_days']} days")
    out += ["", "## Languages", "| lang | files | lines |", "|---|---|---|"]
    out += [f"| {l['lang']} | {l['files']} | {l['lines']} |" for l in facts["languages"][:12]]
    if facts["frameworks"]:
        out += ["", "## Detected frameworks / libraries"]
        out += [f"- {f['name']} ({f['category']}) — from {', '.join(f['sources'][:3])}" for f in facts["frameworks"]]
    out += ["", "## Manifests"]
    for m in facts["manifests"][:15]:
        line = f"- `{m['path']}` [{m['type']}]"
        if m.get("name"):
            line += f" name={m['name']}"
        if m.get("scripts"):
            line += " scripts: " + ", ".join(f"{k}=`{v}`" for k, v in list(m["scripts"].items())[:8])
        out.append(line)
    inf = facts["infra"]
    out += ["", "## Infra & ops"]
    out += [f"- Dockerfile `{d['path']}` FROM {', '.join(d['from'])} EXPOSE {', '.join(d['expose'])} CMD {d['cmd']}" for d in inf["dockerfiles"][:5]]
    out += [f"- Compose `{c['path']}` services: {', '.join(c['services'])}" for c in inf["compose_services"][:3]]
    out += [f"- CI `{c['path']}` ({c['system']})" for c in inf["ci"][:8]]
    out += [f"- Make targets `{m['path']}`: {', '.join(m['targets'][:15])}" for m in inf["makefile_targets"][:2]]
    out += [f"- Env keys `{e['path']}`: {', '.join(e['keys'][:30])}" for e in inf["env_keys"][:2]]
    if inf["k8s_kinds"]:
        out.append(f"- Kubernetes kinds: {inf['k8s_kinds']}")
    if inf["terraform_resources"]:
        out.append(f"- Terraform resources: {inf['terraform_resources']}")
    if inf["openapi_specs"]:
        out.append(f"- OpenAPI specs: {', '.join(inf['openapi_specs'][:5])}")
    if inf["prisma_models"]:
        out.append(f"- Prisma models: {', '.join(inf['prisma_models'][:30])}")
    if inf["migrations"]:
        out.append(f"- Migration files: {inf['migrations']}")
    out += ["", "## Entry point candidates"] + [f"- `{e}`" for e in facts["entry_points"][:20]]
    if facts["routes"]:
        out += ["", f"## Routes / pages detected ({len(facts['routes'])}, first 80)"]
        out += [f"- {r['method']} {r['path']}  → `{r['ref']}` [{r['framework']}]" for r in facts["routes"][:80]]
    dg = facts["dependency_graph"]
    if dg["dir_edges"]:
        out += ["", f"## Internal dependency edges between directories (depth {dg['depth']}, top 40)"]
        out += [f"- {e['from']} → {e['to']} ({e['weight']})" for e in dg["dir_edges"][:40]]
        out += ["", "Most depended-on files: " + ", ".join(f"`{x['path']}`({x['fan_in']})" for x in dg["most_depended_on"][:10])]
    if facts["hotspots"]:
        out += ["", "## Hotspots (commits × size, recent window)", "| file | commits | lines | fix commits | authors |", "|---|---|---|---|---|"]
        out += [f"| `{h['path']}` | {h['commits']} | {h['lines']} | {h['fix_commits']} | {h['authors']} |" for h in facts["hotspots"][:15]]
    if g.get("coupling"):
        out += ["", "## Temporal coupling (files that change together)"]
        out += [f"- `{c['a']}` ⇄ `{c['b']}`: {c['shared_commits']} shared commits, degree {c['degree']}" for c in g["coupling"][:10]]
    if g.get("bus_factor"):
        out += ["", "## Bus factor by top-level dir"]
        out += [f"- {b['dir']}: bus factor {b['bus_factor']}, top author share {b['top_share']} ({b['authors']} authors)" for b in g["bus_factor"][:10]]
    mk = facts["markers"]
    out += ["", f"## TODO/FIXME markers: {mk['total']}"] + [f"- `{x['path']}`: {x['count']}" for x in mk["top_files"][:8]]
    if facts["secrets_candidates"]:
        out += ["", f"## Possible secrets (values NOT captured): {len(facts['secrets_candidates'])}"]
        out += [f"- {s['kind']} at `{s['ref']}`" for s in facts["secrets_candidates"][:10]]
    out += ["", "## Largest code files"] + [f"- `{x['path']}` ({x['lines']} lines)" for x in facts["largest_files"][:10]]
    out += ["", "## Directory tree (depth 3)", "```"] + tree_lines([f["path"] for f in facts["files"]]) + ["```"]
    if facts["readme"]:
        head = (read_text(root / facts["readme"], 12_000) or "").splitlines()[:90]
        out += ["", f"## README head (`{facts['readme']}`)", "```"] + head + ["```"]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--git-days", type=int, default=365)
    ap.add_argument("--dep-depth", type=int, default=2, help="directory depth for the aggregated dependency graph")
    args = ap.parse_args()

    root = args.repo.resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    out = args.out.resolve()
    (out / "data").mkdir(parents=True, exist_ok=True)
    facts = collect(root, out, args.git_days, args.dep_depth)
    (out / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=1, default=str))
    (out / "facts_summary.md").write_text(summary_md(facts, root))
    t = facts["totals"]
    print(f"[collect] {facts['repo']['name']}: {t['files']} files, {t['lines']} lines, "
          f"{len(facts['routes'])} routes, {len(facts['dependency_graph']['file_edges'])} import edges, "
          f"{len(facts['hotspots'])} hotspots in {facts['elapsed_sec']}s")
    print(f"[collect] wrote {out / 'facts.json'} and {out / 'facts_summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
