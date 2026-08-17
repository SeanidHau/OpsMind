"""项目初始化验收测试。

该测试不验证业务逻辑，只确认工程骨架完整，确保后续开发从一致的目录开始。
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_required_project_files_exist() -> None:
    """确认项目元数据、配置文件和核心目录已创建。"""
    required_paths = [
        "README.md",
        "pyproject.toml",
        ".env.example",
        ".gitignore",
        "docker-compose.yml",
        "app",
        "docs",
        "scripts",
        "tests",
    ]

    missing_paths = [
        relative_path
        for relative_path in required_paths
        if not (PROJECT_ROOT / relative_path).exists()
    ]

    assert not missing_paths, f"缺少项目初始化文件或目录：{missing_paths}"
