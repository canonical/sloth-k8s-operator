# AGENTS.md - LLM Development Guide for sloth-k8s-operator

This document provides guidance for LLM agents working on this Juju Kubernetes charm repository.

## Repository Overview

**Project**: Sloth K8s Operator - A Juju charm for deploying Sloth (SLI/SLO generator) on Kubernetes
**Language**: Python 3.12
**Framework**: Juju Operator Framework (ops)
**Build Tool**: charmcraft
**Package Manager**: uv
**Task Runner**: tox

## Repository Structure

```
sloth-k8s-operator/
├── src/
│   ├── charm.py              # Main charm logic (SlothOperatorCharm)
│   ├── sloth.py              # Sloth workload management
│   ├── nginx.py              # Nginx reverse proxy
│   ├── nginx_prometheus_exporter.py
│   ├── ingress_configuration.py
│   └── models.py             # Data models (TLSConfig, etc.)
├── lib/
│   └── charms/               # Charm libraries from charmhub
│       └── sloth_k8s/        # This charm's libraries
├── tests/
│   ├── unit/                 # Unit tests (pytest)
│   │   ├── test_charm/
│   │   └── test_workload/
│   └── integration/          # K8s integration tests (pytest + jubilant)
├── charmcraft.yaml           # Charm metadata and build config
├── pyproject.toml            # Python project config
├── tox.ini                   # Test automation config
└── uv.lock                   # Locked dependencies

```

## Development Workflow

### 1. Making Code Changes

#### Key Files and Their Purpose

**src/charm.py**:
- Main entry point: `SlothOperatorCharm` class
- **CRITICAL**: `reconcile()` is called in `__init__` wrapped in try/except
  - This allows graceful handling during install hook when containers aren't ready
  - DO NOT remove the try/except - it prevents install hook failures
- Handles charm lifecycle events and relations
- Orchestrates nginx, sloth, and nginx-exporter workloads

**src/sloth.py**:
- Manages Sloth workload container
- **IMPORTANT**: Sloth is NOT a long-running service - it's a rules generator
  - The `sloth serve` command in Pebble will continuously crash/restart (this is expected)
  - Sloth generates Prometheus recording/alerting rules from SLO specs, then exits
  - The charm runs `sloth generate` to create rules, which are then pushed to Prometheus
  - Testing should focus on the *generated rules in Prometheus*, not sloth process status
- Port: 8080 (proxied via nginx on 7994) - for UI access only
- Version extraction via regex pattern
- **File Paths**: Uses absolute paths for SLO specs and generated rules
  - SLO specs: `/etc/sloth/slos/<filename>.yaml` (input)
  - Generated rules: `/etc/sloth/rules/<filename>.yaml` (output)
  - These are consolidated and provided via `get_alert_rules()` method
- **Error Handling**: Logs stderr from `sloth generate` as warnings, full ExecError details on failure

**Important Patterns**:
- All workload classes have `reconcile()` methods that check `can_connect()` before operations
- Use `Container.push()` for config files
- Use Pebble layers for service management
- Status messages should be user-friendly

### 2. Updating Dependencies

```bash
# Add a new dependency
uv add <package>

# Update lockfile after changes
tox -e lock

# This updates uv.lock - commit this file!
```

**DO NOT**:
- Manually edit `uv.lock`
- Use `pip` directly
- Run `uv` commands with `--frozen` flag for development (only in CI)

### 3. Code Style and Linting

```bash
# Check code style
tox -e lint

# Auto-fix issues
tox -e fmt
```

**Rules**:
- Follow PEP 8
- Use ruff for linting (configured in pyproject.toml)
- Maximum line length: 99 characters
- Use type hints where appropriate
- Keep functions focused and concise

### 4. Running Tests

#### Unit Tests (Fast - Always Run First)

```bash
# Run all unit tests
tox -e unit

# Run specific test file
tox -e unit -- tests/unit/test_charm/test_charm.py

# Run specific test
tox -e unit -- tests/unit/test_charm/test_charm.py::test_healthy_container_events
```

**Unit Test Guidelines**:
- Mock external dependencies (Pebble, containers, relations)
- Use `ops.testing` (formerly scenario) for charm testing
- Patch system calls and file I/O
- Tests should be fast (<5 seconds total)
- Aim for >75% code coverage

**Key Testing Patterns**:
```python
from ops.testing import Context, State, Container

context = Context(charm_type=SlothOperatorCharm)
container = Container("sloth", can_connect=True)
state = State(containers={container})
state_out = context.run(context.on.start(), state)
```

#### Integration Tests (Slow - Requires K8s)

```bash
# Run integration tests (requires microk8s/k8s cluster)
tox -e integration

# These will:
# 1. Build the charm with charmcraft
# 2. Deploy to a test model
# 3. Wait for active status
# 4. Run verification tests
# 5. Clean up
```

**Integration Test Guidelines**:
- Tests use `pytest-jubilant` for Juju interaction
- Each test gets a fresh model
- Default timeout: 600s (can be adjusted)
- Tests run sequentially (--exitfirst flag)
- Require actual K8s cluster access

**Integration Test Structure**:
```python
@pytest.mark.setup
def test_setup(juju: Juju, sloth_charm, sloth_resources):
    juju.deploy(sloth_charm, SLOTH, resources=sloth_resources, trust=True)
    juju.wait(
        lambda status: status.apps[SLOTH].is_active,
        delay=10,
        successes=1,
        timeout=600,
    )
```

**Timeout Guidelines**:
- OCI image pulls can take 2-5 minutes
- First deployment: 300-600 seconds
- Subsequent deployments: 60-120 seconds
- Use `successes=1` for reliability (not 3+)
- Use `delay=10` to give hooks time between checks

**CRITICAL: Juju API Usage in Integration Tests**:
- Use `juju.run(unit, action_name)` for **Juju actions** (returns ActionResult with `.results` dict)
  ```python
  result = juju.run(f"{GRAFANA}/0", "get-admin-password")
  password = result.results.get("admin-password")  # Access via .results dict
  ```
- Use `juju.exec(command, unit=unit)` for **arbitrary shell commands** (returns ExecResult with `.stdout`)
  ```python
  result = juju.exec("curl -s http://localhost:9090/api/v1/rules", unit=f"{PROMETHEUS}/0")
  data = json.loads(result.stdout)  # Access via .stdout
  ```
- **DO NOT** use `juju.run(unit, shell_command)` - this is incorrect API usage

### 5. Building the Charm

```bash
# Build charm package
charmcraft pack

# This creates: sloth-k8s_ubuntu@24.04-amd64.charm (~11MB)
# Build time: ~4 minutes (includes dependency resolution)
```

**Build Process**:
- Uses LXD container for clean build environment
- Installs dependencies from uv.lock
- Creates venv inside charm
- Packs everything into .charm file

**If Build Fails**:
```bash
# Clean up stale LXD containers
lxc --project charmcraft list
lxc --project charmcraft delete <container-name>

# Clear charmcraft cache
rm -rf ~/.local/share/charmcraft/
```

## Common Issues and Solutions

### Issue: Import errors in tests
**Solution**: Ensure `src/` is in Python path. Tests use `conftest.py` to set this up.

### Issue: "cannot perform the following tasks: Start service X"
**Cause**: Container not ready when reconcile() called
**Solution**: Already handled by try/except in `__init__`. If this persists in hooks, check container readiness.

### Issue: Integration test timeout
**Cause**: Waiting for status with `successes=3` while hooks keep running
**Solution**: Use `successes=1` and longer `delay` (10s+)

### Issue: Integration test accessing Juju action results
**Cause**: Using wrong attribute to access action output
**Solution**: Use `result.results.get(key)` for actions, `result.stdout` for exec commands

### Issue: Rules not appearing in Prometheus after SLO changes
**Cause**: Rules need time to: generate → write to file → send via relation → reload in Prometheus  
**Solution**: Add retry logic with delays (10-30s between checks) when verifying Prometheus rules

### Issue: Unit test failures after code change
**Check**:
1. Did you update fixtures in `conftest.py`?
2. Did you mock all container/Pebble calls?
3. Did you update test assertions to match new behavior?

### Issue: Charm goes to error status on K8s
**Debug**:
```bash
# Check logs
juju debug-log --model <model-name> --replay

# Check specific unit
juju ssh --model <model-name> <app>/<unit> bash

# Inside container
kubectl logs <pod-name> -c <container-name>
```

## Testing Best Practices

### Before Committing
```bash
# Run the full test suite
tox -e lint,unit

# If making charm logic changes, also run:
tox -e integration
```

**IMPORTANT**: Always use `tox` to run tests. Do NOT run `pytest` or `python -m pytest` directly, as this bypasses the proper environment setup and dependency isolation.

### When Writing New Tests

**Unit Tests**:
- Test one thing per test function
- Use descriptive names: `test_<what>_<when>_<expected>`
- Mock external dependencies
- Use fixtures for common setup
- Parametrize tests for multiple inputs

**Integration Tests**:
- Test real deployment scenarios
- Verify status messages
- Check version detection
- Test basic functionality
- Clean up resources

### Coverage Expectations
- Overall: >75%
- Core charm.py: >80%
- Workload modules: >75%
- Test coverage less important than test quality

## Key Juju Charm Concepts

### Charm Lifecycle
```
install → config-changed → start → pebble-ready → active
```

### Pebble (Container Manager)
- Manages services in containers
- Uses "layers" for service configuration
- Services defined in YAML format
- `replan()` applies changes

### Relations
- `provides`: What this charm offers (metrics, dashboards)
- `requires`: What this charm needs (ingress, certificates)
- `peers`: Coordination between units (sloth-peers)

### Status
- `waiting`: Not ready yet
- `active`: Fully operational
- `blocked`: Manual intervention needed
- `error`: Something failed

## Important Configuration

### Charm Config (charmcraft.yaml)
```yaml
name: sloth-k8s
containers:
  sloth:
    resource: sloth-image    # ghcr.io/slok/sloth:v0.11.0
  nginx:
    resource: nginx-image
  nginx-prometheus-exporter:
    resource: nginx-prometheus-exporter-image
```

### Sloth Configuration
- **Port**: 8080 (internal), 7994 (nginx proxy)
- **Command**: `sloth serve --listen=:8080 --default-slo-period=30d`
- **Config**: `slo-period` (charm config option)

## Debugging Tips

### Enable Debug Logging
```bash
# In charm code
logger.setLevel(logging.DEBUG)

# In Juju
juju model-config logging-config="<root>=DEBUG"
```

### Check Container Status
```python
# In reconcile methods
if not self._container.can_connect():
    logger.debug("Container not ready yet")
    return
```

### Inspect Pebble Services
```bash
juju exec --unit sloth/0 -- pebble services
juju exec --unit sloth/0 -- pebble logs sloth
```

## Version Control Guidelines

### Commit Messages
- Use conventional commits: `feat:`, `fix:`, `test:`, `docs:`
- Reference issues if applicable
- Keep commits atomic and focused

### Files to Commit
- ✅ All Python source files
- ✅ Tests
- ✅ Configuration files
- ✅ `uv.lock` (locked dependencies)
- ✅ Documentation
- ❌ `*.charm` files (too large)
- ❌ `.tox/`, `__pycache__/`, `.pytest_cache/`
- ❌ `.logs/` (test logs)

## Quick Reference Commands

```bash
# Development cycle
tox -e fmt                    # Format code
tox -e lint                   # Check style
tox -e unit                   # Run unit tests
charmcraft pack              # Build charm
tox -e integration           # Full integration test

# Dependency management
uv add <package>             # Add dependency
tox -e lock                  # Update lockfile

# Testing specific scenarios
tox -e unit -- -k test_name  # Run specific test
tox -e unit -- -v            # Verbose output
tox -e unit -- --pdb         # Debug on failure

# Charm operations
juju deploy ./sloth-k8s*.charm --trust
juju status --watch 1s
juju debug-log --tail
```

## Error Recovery

### If Tests Are Stuck
```bash
# Kill test processes
pkill -f pytest

# Clean up test models
juju models | grep test- | awk '{print $1}' | xargs -I {} juju destroy-model {} --force --no-prompt
```

### If Charm Won't Deploy
1. Check logs: `juju debug-log --replay`
2. Verify resources in charmcraft.yaml match deployment
3. Ensure cluster has resources (CPU, memory)
4. Check container images are accessible

### If Build Fails
1. Clean LXD containers: `lxc --project charmcraft delete <container>`
2. Check disk space: `df -h`
3. Verify charmcraft version: `charmcraft version`
4. Try: `charmcraft clean`

## Performance Notes

- **Unit tests**: ~10-15 seconds
- **Linting**: <1 second  
- **Charm build**: ~4 minutes (first time), ~2 minutes (cached)
- **Integration tests**: ~80-120 seconds per test
- **Dependency updates**: ~20-30 seconds

## Architecture Decisions

### Why try/except in __init__?
During install hook, containers may not be ready. The try/except allows charm initialization to complete, with reconciliation happening on subsequent hooks (pebble-ready, config-changed).

### Why nginx proxy?
- Provides TLS termination
- Allows certificate management
- Consistent with Canonical COS patterns
- Metrics collection via nginx-prometheus-exporter

### Why absolute paths for SLO files?
Using absolute paths (`/etc/sloth/slos/`, `/etc/sloth/rules/`) ensures:
- Consistent file locations across container restarts
- Easy verification in tests and debugging
- Clear separation between input specs and generated rules
- No ambiguity when passing paths to `sloth generate` CLI

## Resources

- **Juju Docs**: https://juju.is/docs
- **Operator Framework**: https://ops.readthedocs.io/
- **Charmcraft**: https://canonical-charmcraft.readthedocs-hosted.com/
- **Sloth**: https://github.com/slok/sloth

## Summary for LLM Agents

**Key Points**:
1. Always run `tox -e lint,unit` before claiming success
2. The try/except in `__init__` is intentional - don't remove it
3. Use `tox` commands, not direct tool invocations
4. Integration tests require K8s and take time
5. Build times are long (~4 min) - be patient
6. Update `uv.lock` via `tox -e lock` after dependency changes
7. Test coverage goal: >75%
8. Integration test timeouts: 600s, successes=1, delay=10s

**Red Flags** (Don't do these):
- ❌ Remove try/except from reconcile() call in `__init__`
- ❌ Use `pip` instead of `uv`
- ❌ Skip unit tests "because integration tests pass"
- ❌ Set integration test timeout <300s
- ❌ Manually edit `uv.lock`
- ❌ Use `successes=3+` in integration tests (causes timeouts)
- ❌ Use `juju.run(unit, shell_command)` - use `juju.exec(command, unit=unit)` instead
- ❌ Access action results via `.stdout` - use `.results.get(key)` instead
- ❌ Expect Sloth rules to appear instantly in Prometheus - add retry logic with delays

**When Stuck**:
1. Check `juju debug-log --replay`
2. Look at `.logs/*.txt` files
3. Run `juju status` to see actual state
4. Verify containers are running: `juju exec --unit sloth/0 -- pebble services`

## SLO Relation Interface

For guidance on how to implement SLO support in your own charm using the Sloth library, see the "Implementing SLO Support in Your Charm" section in the [README.md](README.md).

## SLO Relation Implementation Details

This charm implements the `sloth` relation (interface: `slo`) which allows other charms to provide SLO specifications:

- **SlothRequirer** (in `src/charm.py`): Consumes SLO specs from related charms
- **SlothProvider** (in external charm): Provides SLO specs in Sloth YAML format
- **SLO Processing**: Specs written to `/etc/sloth/slos/`, rules generated to `/etc/sloth/rules/`
- **Prometheus Integration**: Generated rules automatically pushed via `prometheus-scrape` relation
- **Topology Labels**: SLO queries automatically injected with `juju_application`, `juju_model`, `juju_model_uuid`

### Rule Name Transformation

**IMPORTANT**: Prometheus transforms hyphens to underscores in rule group names when loading rules:
- Generated name: `sloth-slo-sli-recordings-my-service-requests-availability`
- Prometheus name: `sloth_slo_sli_recordings_my_service_requests_availability`
- When testing or querying rules, use **underscores**, not hyphens

### Testing SLO Relations

The `tests/integration/slo-test-provider/` directory contains a test charm that demonstrates:
- How to use SlothProvider to provide SLO specs
- Dynamic SLO updates via config changes
- Proper YAML formatting for Sloth specifications

To test manually, deploy the test provider charm alongside Sloth and relate them via the `sloth` relation endpoint.

## Container Images

This charm uses the following OCI images:

- **Sloth**: `ghcr.io/slok/sloth:v0.11.0` (official Sloth image)
- **Nginx**: `ubuntu/nginx:1.24-24.04_beta` ([Canonical OCI Factory](https://github.com/canonical/oci-factory))
- **Nginx Exporter**: `nginx/nginx-prometheus-exporter:1.1.0` (official exporter)
