"""Read-only character discovery shared by the app, preview and swap worker."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import struct
import tempfile

MODEL_SUFFIXES = frozenset({'.mcd', '.mdl'})


class CharacterChoiceRequired(ValueError):
    def __init__(self, selected, candidates):
        self.selected = Path(selected)
        self.candidates = tuple(candidates)
        names = '\n'.join(str(p.relative_to(self.selected)) for p in self.candidates)
        super().__init__('Choose one character from this folder:\n' + names)


def children(directory):
    try:
        return sorted(Path(directory).iterdir(), key=lambda p: p.name.casefold())
    except (OSError, PermissionError):
        return []


def model_files(directory):
    return [p for p in children(directory) if p.is_file() and p.suffix.casefold() in MODEL_SUFFIXES
            and not p.stem.casefold().endswith(('.bak', '.backup', '.old'))]


def named_child(directory, name):
    matches = [p for p in children(directory) if p.name.casefold() == name.casefold()]
    if len(matches) != 1:
        raise ValueError('Expected one %s folder in %s' % (name, directory))
    return matches[0]


def attire_choices(character):
    choices = []
    for folder in children(character):
        if not folder.is_dir() or folder.name.casefold() == 'basemodel':
            continue
        models = model_files(folder)
        if len(models) == 1:
            choices.append(folder.name)
        elif len(models) > 1:
            choices.extend(folder.name + '/' + p.name for p in models)
    return choices


def has_base(character):
    return any(p.is_dir() and p.name.casefold() == 'basemodel' and model_files(p)
               for p in children(character))


def is_character(character, require_base=True):
    return bool(attire_choices(character)) and (has_base(character) or not require_base)


def resolve_character(selected, max_depth=8, max_directories=4096, require_base=True):
    """Accept a root, BaseModel/attire selection, or a bounded wrapper tree.

    Never choose arbitrarily among several characters, follow directory links,
    or wander up into unrelated collections when the selection is invalid.
    """
    path = Path(selected).expanduser().resolve()
    if path.is_file():
        if path.suffix.casefold() not in MODEL_SUFFIXES:
            raise ValueError('Choose an extracted character folder or an MCD/MDL model.')
        path = path.parent
    if not path.is_dir():
        raise ValueError('Choose an existing extracted character folder.')
    if is_character(path, require_base):
        return path
    if not require_base and model_files(path) and path.name.casefold() != 'basemodel':
        return path.parent
    # A user commonly selects BaseModel, Attire or Textures inside a character.
    for parent in list(path.parents)[:3]:
        if is_character(parent, require_base):
            return parent
    found, attire_only, level, visited = [], [], [path], 0
    for _ in range(max_depth):
        following = []
        for folder in level:
            for child in children(folder):
                if not child.is_dir() or child.is_symlink() or child.name.startswith('.'):
                    continue
                visited += 1
                if visited > max_directories:
                    raise ValueError('This folder is too broad. Open its Characters folder or a single character folder.')
                if is_character(child, require_base):
                    found.append(child)
                elif require_base and is_character(child, False):
                    attire_only.append(child)
                elif child.name.casefold() not in {'textures', 'materials', '__pycache__', 'node_modules'}:
                    following.append(child)
        level = following
        if not level:
            break
    found = sorted(set(found), key=lambda p: str(p).casefold())
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise CharacterChoiceRequired(path, found)
    if require_base and (is_character(path, False) or attire_only):
        raise ValueError('Attire models were found, but this pack has no BaseModel. Select a complete character for Base model; use this attire-only pack as the Attire donor.')
    if has_base(path):
        raise ValueError('BaseModel was found, but no attire model was found. Choose the extracted pack containing both BaseModel and an attire folder with an MCD or MDL.')
    raise ValueError('No character found in this extracted folder. Look for BaseModel plus an attire folder containing .mcd or .mdl files. You may select their enclosing Characters folder; ZIP/7z archives must be extracted first.')


def one_model(directory, base=False):
    models = model_files(directory)
    if base:
        preferred = [p for p in models if p.stem.casefold() == 'basemodel']
        if preferred:
            models = preferred
    if len(models) != 1:
        raise ValueError('Choose one model explicitly: %s contains %d MCD/MDL files.' % (directory, len(models)))
    return models[0]


def resolve_attire(character, attire=None, require_base=True):
    root = resolve_character(character or attire, require_base=require_base)
    if attire is None or not str(attire).strip():
        choices = attire_choices(root)
        if len(choices) != 1:
            raise ValueError('Choose one attire for %s.' % root.name)
        attire = choices[0]
    model = Path(attire).expanduser()
    if not model.is_absolute():
        model = root / model
    model = model.resolve()
    if model.is_dir():
        model = one_model(model)
    if not model.is_file() or model.suffix.casefold() not in MODEL_SUFFIXES:
        raise ValueError('Attire MCD/MDL was not found: %s' % model)
    if model.parent.parent != root or model.parent.name.casefold() == 'basemodel':
        raise ValueError('Choose a model inside one of this character\'s attire folders: %s' % model)
    return root, model


def model_header(path, require_26=False):
    with Path(path).open('rb') as handle:
        raw = handle.read(16 * 1024 * 1024)
    if len(raw) < 8:
        raise ValueError('Model file is empty or truncated: %s' % path)
    if raw[:4] == b'MDL!':
        version = struct.unpack_from('<I', raw, 4)[0]
        info = {'format': 'MDL', 'container': None, 'model': version}
    elif raw[:4] == b'MCD!':
        marker = raw.find(b'MDL!MDL!', 12)
        if marker < 0 or marker + 12 > len(raw):
            raise ValueError('This MCD has no readable MDL model header. Extract/convert it with Model Formatter first: %s' % path)
        info = {'format': 'MCD', 'container': struct.unpack_from('<I', raw, 4)[0],
                'model': struct.unpack_from('<I', raw, marker + 8)[0]}
    else:
        raise ValueError('This is not a Visual Concepts MCD/MDL model. Extract the character files first: %s' % path)
    if not 0 < info['model'] < 256:
        raise ValueError('Invalid or unsupported model header: %s' % path)
    if require_26 and info['model'] != 47:
        raise ValueError('Creating swaps requires WWE 2K26 model version47; this file is version%d. Port it to 2K26 in Model Formatter first: %s' % (info['model'], path))
    return info


@contextmanager
def preview_layout(character, attire):
    """Adapt filenames for the unchanged MCD-only Formatter scene enumerator.

    Native model bytes are never converted or renamed in the source. Model
    copies and read-only asset hardlinks live only in a temporary projection;
    the original SDK still validates/loads the file's actual MCD/MDL content.
    """
    root, selected = resolve_attire(character, attire, require_base=False)
    base_dir = named_child(root, 'BaseModel') if has_base(root) else None
    base = one_model(base_dir, base=True) if base_dir else None
    if base:
        model_header(base)
    model_header(selected)
    with tempfile.TemporaryDirectory(prefix='mykelovve-model-layout-') as temporary:
        target = Path(temporary) / 'Characters' / root.name
        target.mkdir(parents=True)
        scopes = ([(base_dir, base)] if base else []) + [(selected.parent, selected)]
        for scope, model in scopes:
            holder = target / ('BaseModel' if scope == base_dir else scope.name)
            for path in scope.rglob('*'):
                if path.is_symlink():
                    continue
                rel = path.relative_to(scope)
                if path.is_dir():
                    (holder / rel).mkdir(parents=True, exist_ok=True)
                elif path.is_file() and path.suffix.casefold() not in MODEL_SUFFIXES:
                    destination = holder / rel
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(path, destination)
                    except OSError:
                        shutil.copy2(path, destination)
            holder.mkdir(parents=True, exist_ok=True)
            alias = holder / (model.stem + '.mcd')
            shutil.copy2(model, alias)
        yield target, selected.parent.name
