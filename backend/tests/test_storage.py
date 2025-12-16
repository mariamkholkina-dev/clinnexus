"""
Unit-тесты для модуля storage.py
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import UploadFile
from io import BytesIO

from app.core.storage import sanitize_filename, save_upload, StoredFile
from app.core.config import settings


class TestSanitizeFilename:
    """Тесты для функции sanitize_filename."""

    def test_simple_filename(self):
        """Тест простого имени файла."""
        assert sanitize_filename("document.pdf") == "document.pdf"

    def test_filename_with_spaces(self):
        """Тест имени файла с пробелами."""
        assert sanitize_filename("my document.pdf") == "my_document.pdf"

    def test_filename_with_special_chars(self):
        """Тест имени файла со специальными символами."""
        # Символы: < > : " | ? * (7 символов) заменяются на подчеркивания
        # Точка перед расширением не заменяется
        assert sanitize_filename("file<>:\"|?*.pdf") == "file_______.pdf"

    def test_filename_with_path(self):
        """Тест имени файла с путем."""
        assert sanitize_filename("/path/to/file.pdf") == "file.pdf"
        assert sanitize_filename("C:\\Windows\\file.pdf") == "file.pdf"

    def test_filename_with_multiple_underscores(self):
        """Тест имени файла с множественными подчеркиваниями."""
        assert sanitize_filename("file___name.pdf") == "file_name.pdf"

    def test_filename_with_leading_trailing_dots(self):
        """Тест имени файла с ведущими/завершающими точками."""
        assert sanitize_filename("...file...") == "file"
        assert sanitize_filename(".hidden.pdf") == "hidden.pdf"

    def test_empty_filename(self):
        """Тест пустого имени файла."""
        assert sanitize_filename("") == "document"
        assert sanitize_filename("...") == "document"

    def test_very_long_filename(self):
        """Тест очень длинного имени файла."""
        long_name = "a" * 300 + ".pdf"
        result = sanitize_filename(long_name)
        assert len(result) <= 255
        assert result.endswith(".pdf")

    def test_unicode_filename(self):
        """Тест имени файла с unicode символами."""
        # Unicode символы заменяются на подчеркивания, но если имя состоит только из подчеркиваний,
        # функция использует дефолтное имя "document"
        assert sanitize_filename("файл.pdf") == "document.pdf"
        # Если есть ASCII символы, unicode заменяется на подчеркивания
        # "中文" - это 2 символа, поэтому 2 подчеркивания
        assert sanitize_filename("file-中文.pdf") == "file-__.pdf"


class TestSaveUpload:
    """Тесты для функции save_upload."""

    @pytest.fixture
    def temp_storage_dir(self, monkeypatch, tmp_path):
        """Временная директория для хранения файлов."""
        storage_dir = tmp_path / "uploads"
        storage_dir.mkdir()
        monkeypatch.setattr(settings, "storage_base_path", str(storage_dir))
        return storage_dir

    @pytest.fixture
    def sample_file_content(self):
        """Пример содержимого файла."""
        return b"Test file content for upload"

    @pytest.fixture
    def sample_upload_file(self, sample_file_content):
        """Создает UploadFile для тестирования."""
        file_obj = BytesIO(sample_file_content)
        upload_file = UploadFile(
            filename="test_document.pdf",
            file=file_obj,
        )
        # Сохраняем оригинальное содержимое для проверок
        upload_file._original_content = sample_file_content
        return upload_file

    @pytest.mark.asyncio
    async def test_save_upload_basic(self, temp_storage_dir, sample_upload_file):
        """Тест базового сохранения файла."""
        doc_version_id = uuid4()

        result = await save_upload(sample_upload_file, doc_version_id)

        # Проверяем результат
        assert isinstance(result, StoredFile)
        assert result.original_filename == "test_document.pdf"
        assert result.size_bytes == len(sample_upload_file._original_content)

        # Проверяем, что файл сохранен
        expected_path = temp_storage_dir / str(doc_version_id) / "test_document.pdf"
        assert expected_path.exists()

        # Проверяем содержимое файла
        saved_content = expected_path.read_bytes()
        assert saved_content == sample_upload_file._original_content

        # Проверяем SHA256
        expected_sha256 = hashlib.sha256(saved_content).hexdigest()
        assert result.sha256 == expected_sha256

    @pytest.mark.asyncio
    async def test_save_upload_with_sanitized_filename(self, temp_storage_dir):
        """Тест сохранения файла с небезопасным именем."""
        doc_version_id = uuid4()
        unsafe_filename = "file with spaces & special chars.pdf"
        file_content = b"Test content"
        upload_file = UploadFile(
            filename=unsafe_filename,
            file=BytesIO(file_content),
        )

        result = await save_upload(upload_file, doc_version_id)

        # Проверяем, что имя файла было очищено
        expected_path = temp_storage_dir / str(doc_version_id) / "file_with_spaces___special_chars.pdf"
        assert expected_path.exists()
        assert result.original_filename == unsafe_filename

    @pytest.mark.asyncio
    async def test_save_upload_large_file(self, temp_storage_dir):
        """Тест сохранения большого файла (стриминг)."""
        doc_version_id = uuid4()
        # Создаем файл размером 1MB
        large_content = b"x" * (1024 * 1024)
        upload_file = UploadFile(
            filename="large_file.bin",
            file=BytesIO(large_content),
        )

        result = await save_upload(upload_file, doc_version_id)

        # Проверяем размер
        assert result.size_bytes == len(large_content)

        # Проверяем SHA256
        expected_sha256 = hashlib.sha256(large_content).hexdigest()
        assert result.sha256 == expected_sha256

        # Проверяем, что файл сохранен
        expected_path = temp_storage_dir / str(doc_version_id) / "large_file.bin"
        assert expected_path.exists()
        assert expected_path.stat().st_size == len(large_content)

    @pytest.mark.asyncio
    async def test_save_upload_no_filename(self, temp_storage_dir):
        """Тест сохранения файла без имени."""
        doc_version_id = uuid4()
        file_content = b"Test content"
        upload_file = UploadFile(
            filename=None,
            file=BytesIO(file_content),
        )

        result = await save_upload(upload_file, doc_version_id)

        # Проверяем, что использовано дефолтное имя
        expected_path = temp_storage_dir / str(doc_version_id) / "document"
        assert expected_path.exists()
        assert result.original_filename == "document"

    @pytest.mark.asyncio
    async def test_save_upload_creates_directory(self, temp_storage_dir):
        """Тест, что функция создает директорию для версии."""
        doc_version_id = uuid4()
        file_content = b"Test content"
        upload_file = UploadFile(
            filename="test.pdf",
            file=BytesIO(file_content),
        )

        version_dir = temp_storage_dir / str(doc_version_id)
        assert not version_dir.exists()

        await save_upload(upload_file, doc_version_id)

        assert version_dir.exists()
        assert version_dir.is_dir()

    @pytest.mark.asyncio
    async def test_save_upload_uri_format(self, temp_storage_dir, sample_upload_file):
        """Тест формата URI."""
        doc_version_id = uuid4()

        result = await save_upload(sample_upload_file, doc_version_id)

        # URI должен быть строкой
        assert isinstance(result.uri, str)
        # URI должен содержать путь к файлу
        assert str(doc_version_id) in result.uri or "test_document.pdf" in result.uri

    @pytest.mark.asyncio
    async def test_save_upload_multiple_files_same_version(self, temp_storage_dir):
        """Тест сохранения нескольких файлов для одной версии."""
        doc_version_id = uuid4()

        file1 = UploadFile(
            filename="file1.pdf",
            file=BytesIO(b"Content 1"),
        )
        file2 = UploadFile(
            filename="file2.pdf",
            file=BytesIO(b"Content 2"),
        )

        result1 = await save_upload(file1, doc_version_id)
        result2 = await save_upload(file2, doc_version_id)

        # Оба файла должны быть сохранены
        path1 = temp_storage_dir / str(doc_version_id) / "file1.pdf"
        path2 = temp_storage_dir / str(doc_version_id) / "file2.pdf"

        assert path1.exists()
        assert path2.exists()
        assert result1.original_filename == "file1.pdf"
        assert result2.original_filename == "file2.pdf"

