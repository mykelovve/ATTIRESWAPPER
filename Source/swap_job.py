"""Plain Python job utilities shared by the desktop UI and Blender worker.

This module deliberately has no Blender or formatter imports. Source models are
read-only; a worker publishes a fresh output directory only after export passes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import subprocess
from typing import Any


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


from model_layout import (named_child as _child, resolve_character as find_character,
                          one_model as _one_mcd, resolve_attire, attire_choices,
                          model_header)


def discover_attires(character):
    root = find_character(character)
    return [str(resolve_attire(root, choice)[1]) for choice in attire_choices(root)]


def mcd_version(path):
    return model_header(path, require_26=True)


def validate_job(config: dict[str, Any]):
    base, base_attire = resolve_attire(config.get('base_character'), config.get('base_attire'))
    donor, donor_attire = resolve_attire(config.get('donor_character'), config.get('donor_attire'), require_base=False)
    base_model = _one_mcd(_child(base, 'BaseModel'), base=True)
    addon = Path(config['addon_dir']).expanduser().resolve()
    if addon.name != 'WWE2K_MDL' and (addon / 'WWE2K_MDL').is_dir():
        addon = addon / 'WWE2K_MDL'
    if not (addon / '__init__.py').is_file() or not (addon / 'Link' / 'VCModel.dll').is_file():
        raise ValueError('Choose the complete WWE2K_MDL addon folder (including Link/VCModel.dll)')
    helpers = Path(config.get('helpers_dir') or Path(__file__).parent).resolve()
    for name in ('merge_helpers.py', 'fit_helpers.py', 'garment_fit.py'):
        if not (helpers / name).is_file():
            raise ValueError('Missing bundled helper: %s' % (helpers / name))
    output = Path(config['output_dir']).expanduser().resolve()
    for source in (base, donor):
        if output == source or source in output.parents or output in source.parents:
            raise ValueError('Output must be separate from both source character folders')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError('Choose a fresh empty output folder: %s' % output)
    versions = {name: mcd_version(path) for name, path in
                (('base_model', base_model), ('base_attire', base_attire), ('donor_attire', donor_attire))}
    return {'base_character': base, 'base_attire': base_attire, 'base_model': base_model,
            'donor_character': donor, 'donor_attire': donor_attire, 'output_dir': output,
            'addon_dir': addon, 'helpers_dir': helpers, 'versions': versions,
            'fit_clothing': bool(config.get('fit_clothing', True)),
            'repair_eyelash': bool(config.get('repair_eyelash', True))}


def blender_command(blender_exe, worker_script, job_path):
    return [str(blender_exe), '--background', '--factory-startup', '--disable-autoexec',
            '--python-exit-code', '1', '--python', str(worker_script), '--', str(job_path)]


def write_job(path, config):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding='utf-8')
    return path


def run_job(blender_exe, worker_script, job_path, **kwargs):
    """Popen wrapper; callers own stdout consumption and cancellation."""
    return subprocess.Popen(blender_command(blender_exe, worker_script, job_path),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace', **kwargs)
