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

### Architecture Decisions

### Why try/except in __init__?
During install hook, containers may not be ready. The try/except allows charm initialization to complete, with reconciliation happening on subsequent hooks (pebble-ready, config-changed).

### Why nginx proxy?
- Provides TLS termination
- Allows certificate management
- Consistent with Canonical COS patterns
- Metrics collection via nginx-prometheus-exporter

### Why simplify from Parca?
Sloth is simpler than Parca - it doesn't need:
- S3 storage (no profile persistence)
- Scraping configs (generates rules, doesn't scrape)
- gRPC (HTTP only)
- Complex persistence options

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

## Current Work Status - SLO Provider/Requirer Library

### ✅ COMPLETED - All Tasks Finished (2026-01-23)

#### Phase 1: SLO Library Implementation (2026-01-08)

1. **SLO Charm Library** (`lib/charms/sloth_k8s/v0/slo.py`)
   - ✅ Created SLOProvider class for charms to provide SLO specs
   - ✅ Created SLORequirer class for Sloth to consume SLO specs
   - ✅ Implemented Pydantic validation (SLOSpec model)
   - ✅ Added SLOsChangedEvent for dynamic updates
   - ✅ Comprehensive documentation in module docstring

2. **Sloth Workload Updates** (`src/sloth.py`)
   - ✅ Added `additional_slos` parameter to constructor
   - ✅ Implemented `_reconcile_additional_slos()` method
   - ✅ Each SLO spec is written to `/etc/sloth/slos/` in container
   - ✅ Rules generated via `sloth generate` for each SLO
   - ✅ Rules saved to `/etc/sloth/rules/` in container
   - ✅ Maintains backward compatibility (hardcoded Prometheus SLO)

3. **Charm Integration** (`src/charm.py`)
   - ✅ Added SLORequirer instance
   - ✅ Observes `slos_changed` event
   - ✅ Passes collected SLOs to Sloth workload during reconciliation
   - ✅ New event handler: `_on_slos_changed()`

4. **Charm Metadata** (`charmcraft.yaml`)
   - ✅ Added `slos` relation (requires side, interface: slo)
   - ✅ Marked as optional to maintain backward compatibility

#### Phase 2: Test Fixes and Refinements (2026-01-23)

5. **Integration Test Fixes** (commits: `932ee70`, `bfc9c07`, `f3b2dc8`)
   - ✅ Fixed `juju.run()` vs `juju.exec()` API usage patterns
   - ✅ Fixed action result access (`.results.get()` instead of parsing stdout)
   - ✅ Added retry logic for Prometheus rule verification (rules take time to propagate)
   - ✅ Fixed slo-test-provider charm build and metadata
   - ✅ Fixed absolute path usage for SLO files (prevents file not found errors)
   - ✅ Improved error logging (capture stderr from `sloth generate`)

6. **Code Quality** (commit: `c2d73fb`)
   - ✅ Removed unnecessary `lib/charms/sloth_k8s/v0/__init__.py`
   - ✅ Added type: ignore comments for SLOProviderEvents/SLORequirerEvents
   - ✅ Bumped LIBPATCH to 5 for library updates

7. **TLS Removal** (commit: `038c0ef`)
   - ✅ Removed TLS-related code (Sloth has no external endpoints)
   - ✅ Removed models.py, _tls_config property, _reconcile_tls_config method
   - ✅ Simplified Sloth constructor (no tls_config parameter)
   - ✅ Added missing `catalogue` and `ingress` relations to metadata

### 📊 Final Metrics

- **Total Unit Tests**: 124 passing (no change)
- **Code Coverage**: 76% (target: >75%)
- **Lint Errors**: 0
- **Integration Tests**: 12/12 passing (all fixed!)
- **Build Status**: Clean

### 🎯 Next Steps for SLO Provider Implementation

To implement SLO support in a charm that defines its own SLI/SLO expressions, you need:

1. **Add the SLO library dependency** to your `pyproject.toml` or `requirements.txt`:
   ```toml
   # In pyproject.toml
   dependencies = [
       "charmlibs-interfaces-slo @ git+https://github.com/canonical/charmlibs.git@main#subdirectory=interfaces/slo",
   ]
   ```
   
   Or for requirements.txt:
   ```
   charmlibs-interfaces-slo @ git+https://github.com/canonical/charmlibs.git@main#subdirectory=interfaces/slo
   ```

2. **Import and instantiate SLOProvider**:
   ```python
   from charmlibs.interfaces.slo import SLOProvider
   
   class YourCharm(CharmBase):
       def __init__(self, *args):
           super().__init__(*args)
           self.slo_provider = SLOProvider(self)
   ```

3. **Define your SLO specification** following Sloth's format (as YAML string):
   ```python
   slo_yaml = """
   version: prometheus/v1
   service: your-service-name
   labels:
     team: your-team
   slos:
     - name: availability
       objective: 99.9
       description: "99.9% availability"
       sli:
         events:
           error_query: 'sum(rate(http_requests_total{status=~"5.."}[{{.window}}]))'
           total_query: 'sum(rate(http_requests_total[{{.window}}]))'
       alerting:
         name: YourServiceHighErrorRate
         labels:
           severity: page
   """
   ```

4. **Provide the SLO spec** when appropriate (e.g., on pebble-ready, config-changed):
   ```python
   self.slo_provider.provide_slos(slo_yaml)
   ```

5. **Add metadata** in your charm's `charmcraft.yaml`:
   ```yaml
   provides:
     slos:
       interface: slo
   ```

6. **Relate to Sloth**:
   ```bash
   juju relate your-charm:slos sloth:slos
   ```

The SLO library is now ready for use!

---

## ✅ TASK COMPLETED: Fix Linting Errors

**Status**: COMPLETED  
**Date**: 2026-01-07  
**Errors Fixed**: 27 → 0

### Changes Made

1. **Fixed Duplicate Test Functions**
   - Removed duplicate test definitions in `test_sloth.py` (lines 311-416)
   - Tests: `test_reconcile_additional_slos`, `test_reconcile_additional_slos_generates_rules`, `test_reconcile_multiple_additional_slos`

2. **Fixed Unused Variables**
   - Replaced `state_out = ` with `_ = ` where appropriate
   - Fixed `result` variable in test functions (kept where needed, replaced where not)

3. **Fixed Whitespace Issues**
   - Removed trailing whitespace from all files
   - Cleaned up blank lines with spaces in `slo.py`

4. **Fixed Import Order**
   - Moved Pydantic import to top of file (after module docstring)
   - Removed duplicate LIBID/LIBAPI/LIBPATCH definitions

### Final Result

```bash
$ tox -e lint
All checks passed!
  lint: OK (1.03=setup[0.14]+cmd[0.90] seconds)
  congratulations :) (1.18 seconds)
```

**Zero lint errors remaining!** ✅

### Updated Priority Order

1. ~~**HIGH**: Fix linting errors~~ ✅ **DONE**
2. **HIGH**: Complete library unit tests (proper Context API usage)
3. **MEDIUM**: Update charm unit tests (rewrite for sloth)
4. **MEDIUM**: Run and verify integration tests
5. **LOW**: Documentation updates

---

## Manual Verification Guide

This section describes how to manually verify the complete SLO functionality end-to-end.

### Prerequisites

- Juju controller bootstrapped on microk8s or k8s cluster
- `juju`, `kubectl`, and `charmcraft` installed
- Built sloth-k8s charm and slo-test-provider charm

### Step 1: Build Charms

```bash
# Build sloth-k8s charm
cd /home/ubuntu/Code/sloth-k8s-operator
charmcraft pack

# Build test SLO provider charm
cd tests/integration/slo-test-provider
charmcraft pack
cd ../../..
```

Expected result:
- `sloth-k8s_ubuntu@24.04-amd64.charm` (~11MB)
- `tests/integration/slo-test-provider/slo-test-provider_ubuntu-22.04-amd64.charm` (~10MB)

### Step 2: Deploy Observability Stack

```bash
# Create a test model
juju add-model sloth-manual-test

# Deploy Prometheus
juju deploy prometheus-k8s prometheus --channel 1/stable --trust

# Deploy Grafana
juju deploy grafana-k8s grafana --channel 1/stable --trust

# Relate Prometheus and Grafana
juju integrate grafana:grafana-source prometheus:grafana-source

# Wait for active status
juju wait-for application prometheus --query='status=="active"' --timeout=10m
juju wait-for application grafana --query='status=="active"' --timeout=10m
```

### Step 3: Deploy Sloth

```bash
# Deploy sloth-k8s
juju deploy ./sloth-k8s_ubuntu@24.04-amd64.charm sloth --trust \
  --resource sloth-image=ghcr.io/slok/sloth:v0.11.0

# Relate sloth to observability stack
juju integrate sloth:metrics-endpoint prometheus:metrics-endpoint
juju integrate sloth:grafana-dashboard grafana:grafana-dashboard

# Wait for active status
juju wait-for application sloth --query='status=="active"' --timeout=10m
```

### Step 4: Deploy SLO Provider

```bash
# Deploy test provider with custom SLO config
juju deploy ./tests/integration/slo-test-provider/slo-test-provider_ubuntu-22.04-amd64.charm \
  slo-test-provider \
  --resource test-app-image=ubuntu:22.04 \
  --config slo-service-name="my-test-service" \
  --config slo-objective="99.5"

# Wait for active status
juju wait-for application slo-test-provider --query='status=="active"' --timeout=5m

# Relate provider to sloth (using the new 'sloth' relation name)
juju integrate slo-test-provider:sloth sloth:sloth

# Wait for relation to establish
sleep 30
```

### Step 5: Verify SLO Relation

```bash
# Check relation status
juju status --relations

# Expected output should show:
# sloth:sloth <-> slo-test-provider:sloth (interface: slo)
```

### Step 6: Verify SLO Rules in Sloth Container

```bash
# Check SLO spec files in sloth container
juju exec --unit sloth/0 -- ls -la /etc/sloth/slos/

# Should show:
# - prometheus_sloth_sli_plugin.yaml (built-in SLO for Prometheus availability)
# - my-test-service.yaml (from slo-test-provider)

# View the generated SLO spec
juju exec --unit sloth/0 -- cat /etc/sloth/slos/my-test-service.yaml

# Check generated rules
juju exec --unit sloth/0 -- ls -la /etc/sloth/rules/

# Should show:
# - prometheus_sloth_sli_plugin.yaml
# - my-test-service.yaml

# View generated Prometheus rules
juju exec --unit sloth/0 -- cat /etc/sloth/rules/my-test-service.yaml
```

Expected rule format:
```yaml
groups:
  - name: sloth-slo-sli-recordings-my-test-service-requests-availability
    rules:
      - record: slo:sli_error:ratio_rate5m
        expr: ...
```

### Step 7: Verify Rules in Prometheus

```bash
# Get Prometheus pod name
PROM_POD=$(kubectl -n sloth-manual-test get pods -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}')

# Check Prometheus rules API
kubectl -n sloth-manual-test exec $PROM_POD -- \
  curl -s http://localhost:9090/api/v1/rules | jq '.data.groups[] | select(.name | contains("my-test-service"))'

# Or use juju exec
juju exec --unit prometheus/0 -- \
  curl -s http://localhost:9090/api/v1/rules | grep -A20 "my-test-service"
```

Expected output: Rules for "my-test-service" should appear in Prometheus.

**Note**: It may take 30-60 seconds for rules to propagate from Sloth → relation → Prometheus → reload. Use retry logic if rules don't appear immediately.

### Step 8: Verify Grafana Dashboard

```bash
# Get Grafana admin password
GRAFANA_PASSWORD=$(juju run grafana/0 get-admin-password --format=json | jq -r '.["grafana/0"].results["admin-password"]')

# Port-forward Grafana
kubectl -n sloth-manual-test port-forward svc/grafana-k8s 3000:3000 &

# Open browser to http://localhost:3000
# Login: admin / <GRAFANA_PASSWORD>
# Navigate to Dashboards → Sloth dashboard should be present
```

### Step 9: Test Dynamic SLO Updates

```bash
# Change SLO objective
juju config slo-test-provider slo-objective="99.9"

# Wait for relation to update
sleep 20

# Verify updated SLO in sloth container
juju exec --unit sloth/0 -- cat /etc/sloth/slos/my-test-service.yaml | grep objective

# Should show: objective: 99.9
```

### Step 10: Cleanup

```bash
# Destroy test model
juju destroy-model sloth-manual-test --destroy-storage --no-prompt --force

# Or just remove applications
juju remove-application sloth
juju remove-application slo-test-provider
juju remove-application prometheus
juju remove-application grafana
```

### Troubleshooting

**Problem**: Rules not appearing in Prometheus

**Solutions**:
1. Wait longer (up to 60 seconds for propagation)
2. Check sloth logs: `juju debug-log --replay --include sloth`
3. Verify relation data: 
   ```bash
   juju show-unit slo-test-provider/0 --format=json | jq '.["slo-test-provider/0"]["relation-info"]'
   ```
4. Check for errors in Prometheus: `juju debug-log --replay --include prometheus`

**Problem**: Sloth charm goes to error state

**Solutions**:
1. Check logs: `juju debug-log --replay --include sloth`
2. Verify container readiness: `juju exec --unit sloth/0 -- pebble services`
3. Check SLO generation errors: `juju exec --unit sloth/0 -- pebble logs sloth | tail -50`

**Problem**: SLO provider not providing SLOs

**Solutions**:
1. Check relation status: `juju status --relations`
2. Verify config: `juju config slo-test-provider`
3. Check provider logs: `juju debug-log --replay --include slo-test-provider`

### Expected Metrics and Verification

After successful deployment and relation, you should see:

1. **In Sloth Container**:
   - `/etc/sloth/slos/` contains SLO spec files
   - `/etc/sloth/rules/` contains generated Prometheus rules
   - At least 2 SLO files: built-in Prometheus SLO + provider SLOs

2. **In Prometheus**:
   - Rules groups named `sloth-slo-sli-recordings-<service>-<slo-name>`
   - Recording rules like `slo:sli_error:ratio_rate5m`
   - Alert rules like `<ServiceName>HighErrorRate`

3. **In Grafana**:
   - Sloth dashboard available
   - Panels showing SLO metrics (if metrics are being generated)

4. **Juju Status**:
   - All applications in `active` status
   - Relation `sloth:sloth <-> slo-test-provider:sloth` established
   - No error messages in unit workload status
