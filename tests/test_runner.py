"""Tests for container management functions in datavia.runner."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from datavia.runner import (get_container_status, start_container,
                            stop_container)


class TestContainerManagement:
    """Test individual container management functions."""

    @patch("subprocess.run")
    def test_get_container_status_running(self, mock_run):
        """Test get_container_status when container is running."""
        # Mock docker compose ps returning container IDs
        mock_run.return_value.stdout = "abc123\ndef456\n"
        mock_run.return_value.returncode = 0

        result = get_container_status()

        assert result is True
        mock_run.assert_called_once_with(
            ["docker", "compose", "ps", "-q"],
            cwd="/home/bergmi/tree-D_data-integration/datavia/datavia",
            capture_output=True,
            text=True,
            check=True,
        )

    @patch("subprocess.run")
    def test_get_container_status_not_running(self, mock_run):
        """Test get_container_status when container is not running."""
        # Mock docker compose ps returning empty output
        mock_run.return_value.stdout = ""
        mock_run.return_value.returncode = 0

        result = get_container_status()

        assert result is False
        mock_run.assert_called_once_with(
            ["docker", "compose", "ps", "-q"],
            cwd="/home/bergmi/tree-D_data-integration/datavia/datavia",
            capture_output=True,
            text=True,
            check=True,
        )

    @patch("subprocess.run")
    def test_get_container_status_docker_not_available(self, mock_run):
        """Test get_container_status when Docker is not available."""
        # Mock FileNotFoundError (docker command not found)
        mock_run.side_effect = FileNotFoundError("docker command not found")

        with pytest.raises(FileNotFoundError):
            get_container_status()

    @patch("subprocess.run")
    def test_get_container_status_docker_error(self, mock_run):
        """Test get_container_status when docker command fails."""
        # Mock subprocess.CalledProcessError
        mock_run.side_effect = subprocess.CalledProcessError(1, "docker compose ps")

        result = get_container_status()

        # Should return False when docker command fails
        assert result is False

    @patch("time.sleep")  # Mock sleep to speed up tests
    @patch("subprocess.run")
    def test_start_container_success(self, mock_run, mock_sleep):
        """Test start_container successful startup."""
        mock_run.return_value.returncode = 0

        # Should not raise any exceptions
        start_container()

        mock_run.assert_called_once_with(
            ["docker", "compose", "up", "-d"],
            cwd="/home/bergmi/tree-D_data-integration/datavia/datavia",
            check=True,
        )
        mock_sleep.assert_called_once_with(2)

    @patch("subprocess.run")
    def test_start_container_docker_compose_fails(self, mock_run):
        """Test start_container handles docker-compose failure."""
        # Mock docker-compose failure
        mock_run.side_effect = subprocess.CalledProcessError(1, "docker-compose")

        with pytest.raises(subprocess.CalledProcessError):
            start_container()

        mock_run.assert_called_once_with(
            ["docker", "compose", "up", "-d"],
            cwd="/home/bergmi/tree-D_data-integration/datavia/datavia",
            check=True,
        )

    @patch("subprocess.run")
    def test_start_container_no_docker_compose(self, mock_run):
        """Test start_container when docker-compose not available."""
        # Mock docker-compose not found
        mock_run.side_effect = FileNotFoundError("docker-compose command not found")

        with pytest.raises(FileNotFoundError):
            start_container()

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_stop_container_success(self, mock_run, mock_sleep):
        """Test stop_container successful shutdown."""
        mock_run.return_value.returncode = 0

        # Should not raise any exceptions
        stop_container()

        # Should call stop and down commands
        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["docker", "compose", "stop", "-t", "10"],
            cwd="/home/bergmi/tree-D_data-integration/datavia/datavia",
            check=True,
        )
        mock_run.assert_any_call(
            ["docker", "compose", "down"],
            cwd="/home/bergmi/tree-D_data-integration/datavia/datavia",
            check=True,
        )

    @patch("subprocess.run")
    def test_stop_container_docker_compose_fails(self, mock_run):
        """Test stop_container handles docker-compose failure with cleanup."""
        # Mock first command (stop) failing, then cleanup commands
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "docker-compose"),  # stop fails
            None,  # cleanup down command succeeds
        ]

        with pytest.raises(subprocess.CalledProcessError):
            stop_container()

        # Should attempt cleanup even after failure
        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["docker", "compose", "down", "--remove-orphans"],
            cwd="/home/bergmi/tree-D_data-integration/datavia/datavia",
        )


class TestContainerIntegration:
    """Test integrated container lifecycle scenarios."""

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_container_lifecycle(self, mock_run, mock_sleep):
        """Test complete container start/stop lifecycle."""

        # Mock different responses for each call
        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0]
            if cmd == ["docker", "compose", "ps", "-q"]:
                # Return different results for status checks
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
            else:
                # For compose up/down commands
                result = MagicMock()
                result.returncode = 0
                return result

        mock_run.side_effect = mock_run_side_effect

        # Initial state: not running
        assert get_container_status() is False

        # Start containers
        start_container()

        # Should now be running
        assert get_container_status() is True

        # Stop containers
        stop_container()

        # Should be stopped
        assert get_container_status() is False
