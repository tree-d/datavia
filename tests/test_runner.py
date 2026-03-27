"""Tests for container management functions in datavia.runner."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import datavia.runner as runner_module
from datavia.runner import (
    compose_dir,
    get_container_status,
    start_container,
    stop_container,
)

# Use the actual compose_dir resolved by runner.py (Path(__file__).parent of runner)
# so tests remain correct regardless of where pytest is invoked from.
_cwd = str(compose_dir)


class TestContainerManagement:
    """Test individual container management functions."""

    @patch("datavia.runner._env_file_args", return_value=[])
    @patch("subprocess.run")
    def test_get_container_status_running(self, mock_run, _mock_env):
        """Test get_container_status when container is running."""
        mock_run.return_value.stdout = "abc123\ndef456\n"
        mock_run.return_value.returncode = 0

        result = get_container_status()

        assert result is True
        mock_run.assert_called_once_with(
            ["docker", "compose", "ps", "-q"],
            cwd=_cwd,
            capture_output=True,
            text=True,
            check=True,
        )

    @patch("datavia.runner._env_file_args", return_value=[])
    @patch("subprocess.run")
    def test_get_container_status_not_running(self, mock_run, _mock_env):
        """Test get_container_status when container is not running."""
        mock_run.return_value.stdout = ""
        mock_run.return_value.returncode = 0

        result = get_container_status()

        assert result is False
        mock_run.assert_called_once_with(
            ["docker", "compose", "ps", "-q"],
            cwd=_cwd,
            capture_output=True,
            text=True,
            check=True,
        )

    @patch("datavia.runner._env_file_args", return_value=[])
    @patch("subprocess.run")
    def test_get_container_status_docker_not_available(self, mock_run, _mock_env):
        """Test get_container_status when Docker is not available."""
        mock_run.side_effect = FileNotFoundError("docker command not found")

        with pytest.raises(FileNotFoundError):
            get_container_status()

    @patch("datavia.runner._env_file_args", return_value=[])
    @patch("subprocess.run")
    def test_get_container_status_docker_error(self, mock_run, _mock_env):
        """Test get_container_status when docker command fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "docker compose ps")

        result = get_container_status()

        assert result is False

    @patch("datavia.runner._env_file_args", return_value=[])
    @patch("time.sleep")
    @patch("subprocess.run")
    def test_start_container_success(self, mock_run, mock_sleep, _mock_env):
        """Test start_container successful startup."""
        mock_run.return_value.returncode = 0

        start_container()

        mock_run.assert_called_once_with(
            ["docker", "compose", "up", "-d"],
            cwd=_cwd,
            check=True,
        )
        mock_sleep.assert_called_once_with(2)

    @patch("datavia.runner._env_file_args", return_value=[])
    @patch("subprocess.run")
    def test_start_container_docker_compose_fails(self, mock_run, _mock_env):
        """Test start_container handles docker-compose failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "docker-compose")

        with pytest.raises(subprocess.CalledProcessError):
            start_container()

        mock_run.assert_called_once_with(
            ["docker", "compose", "up", "-d"],
            cwd=_cwd,
            check=True,
        )

    @patch("datavia.runner._env_file_args", return_value=[])
    @patch("subprocess.run")
    def test_start_container_no_docker_compose(self, mock_run, _mock_env):
        """Test start_container when docker-compose not available."""
        mock_run.side_effect = FileNotFoundError("docker-compose command not found")

        with pytest.raises(FileNotFoundError):
            start_container()

    @patch("datavia.runner._env_file_args", return_value=[])
    @patch("time.sleep")
    @patch("subprocess.run")
    def test_stop_container_success(self, mock_run, mock_sleep, _mock_env):
        """Test stop_container successful shutdown."""
        mock_run.return_value.returncode = 0

        stop_container()

        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["docker", "compose", "stop", "-t", "10"],
            cwd=_cwd,
            check=True,
        )
        mock_run.assert_any_call(
            ["docker", "compose", "down"],
            cwd=_cwd,
            check=True,
        )

    @patch("datavia.runner._env_file_args", return_value=[])
    @patch("subprocess.run")
    def test_stop_container_docker_compose_fails(self, mock_run, _mock_env):
        """Test stop_container handles docker-compose failure with cleanup."""
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "docker-compose"),  # stop fails
            None,  # cleanup down command succeeds
        ]

        with pytest.raises(subprocess.CalledProcessError):
            stop_container()

        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["docker", "compose", "down", "--remove-orphans"],
            cwd=_cwd,
            check=False,
        )

    def test_env_file_args_present(self, tmp_path):
        """Test _env_file_args returns --env-file flag when .env exists in cwd."""
        env_file = tmp_path / ".env"
        env_file.write_text("POSTGRES_PASSWORD=test\n")
        # Provide a home dir that has no .datavia/.env so cwd candidate wins.
        no_home = tmp_path / "home_no_env"
        no_home.mkdir()

        with (
            patch.object(Path, "cwd", return_value=tmp_path),
            patch.object(Path, "home", return_value=no_home),
        ):
            result = runner_module._env_file_args()

        assert result == ["--env-file", str(env_file)]

    def test_env_file_args_absent(self, tmp_path):
        """Test _env_file_args returns empty list when no .env file exists."""
        no_home = tmp_path / "home_no_env"
        no_home.mkdir()

        with (
            patch.object(Path, "cwd", return_value=tmp_path),
            patch.object(Path, "home", return_value=no_home),
        ):
            result = runner_module._env_file_args()

        assert result == []


class TestContainerIntegration:
    """Test integrated container lifecycle scenarios."""

    @patch("datavia.runner._env_file_args", return_value=[])
    @patch("time.sleep")
    @patch("subprocess.run")
    def test_container_lifecycle(self, mock_run, mock_sleep, _mock_env):
        """Test complete container start/stop lifecycle."""

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0]
            # Match the ps command regardless of trailing args
            if "ps" in cmd and "-q" in cmd:
                if not hasattr(mock_run_side_effect, "call_count"):
                    mock_run_side_effect.call_count = 0
                mock_run_side_effect.call_count += 1

                result = MagicMock()
                result.returncode = 0
                if mock_run_side_effect.call_count == 1:
                    result.stdout = ""  # not running initially
                elif mock_run_side_effect.call_count == 2:
                    result.stdout = "abc123"  # running after start
                else:
                    result.stdout = ""  # stopped after stop
                return result
            result = MagicMock()
            result.returncode = 0
            return result

        mock_run.side_effect = mock_run_side_effect

        assert get_container_status() is False
        start_container()
        assert get_container_status() is True
        stop_container()
        assert get_container_status() is False
