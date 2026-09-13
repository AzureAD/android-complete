"""Module-owned, invocation-only parameter schemas; never persisted release state."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import MISSING, dataclass, fields, is_dataclass
import json
import math
from types import MappingProxyType, UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints


class ParameterError(ValueError):
    pass


@dataclass(frozen=True)
class NoParameters:
    """Default input model for a hook without public parameters."""


@dataclass(frozen=True)
class ParameterField:
    name: str
    annotation: object
    default: object = MISSING


def _model_fields(model):
    if not isinstance(model, type) or not is_dataclass(model) or not model.__dataclass_params__.frozen:
        raise ParameterError("parameter model must be a frozen dataclass type")
    hints = get_type_hints(model)
    result = []
    for item in fields(model):
        if not item.init:
            raise ParameterError(f"{item.name}: parameter fields must participate in initialization")
        annotation = hints.get(item.name)
        _supported(annotation, item.name)
        default = item.default
        if item.default_factory is not MISSING:
            default = item.default_factory()
        if default is not MISSING:
            default = _validate(annotation, default, item.name)
        result.append(ParameterField(item.name, annotation, default))
    return tuple(result)


def _supported(annotation, path):
    origin, args = get_origin(annotation), get_args(annotation)
    if annotation in (str, bool, int, float, type(None)):
        return
    if origin in (Union, UnionType, Literal):
        if origin is Literal:
            if not args or any(type(value) not in (str, bool, int, float, type(None)) for value in args):
                raise ParameterError(f"{path}: unsupported Literal values")
            return
        for item in args:
            _supported(item, path)
        return
    if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
        _supported(args[0], path)
        return
    if origin is Mapping and len(args) == 2 and args[0] is str:
        _supported(args[1], path)
        return
    raise ParameterError(
        f"{path}: unsupported parameter type {annotation!r}; use scalar, Literal, "
        "union, tuple[T, ...], or Mapping[str, T]"
    )


def _validate(annotation, value, path):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, UnionType):
        for item in args:
            try:
                return _validate(item, value, path)
            except ParameterError:
                pass
    elif origin is Literal:
        if any(type(value) is type(choice) and value == choice for choice in args):
            return value
    elif origin is tuple:
        if isinstance(value, (list, tuple)):
            return tuple(_validate(args[0], item, f"{path}[{index}]") for index, item in enumerate(value))
    elif origin is Mapping:
        if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
            return MappingProxyType({key: _validate(args[1], item, f"{path}.{key}")
                                     for key, item in value.items()})
    elif type(value) is annotation:
        if annotation is float and not math.isfinite(value):
            raise ParameterError(f"{path}: must be finite")
        return value
    raise ParameterError(f"{path}: expected {annotation!r}, got {type(value).__name__}")


def _cli_value(annotation, text):
    if not isinstance(text, str):
        raise ParameterError("CLI parameter values must be text")
    # Text models keep literal strings, including 'false', '123' and 'null'.
    options = get_args(annotation) if get_origin(annotation) in (Union, UnionType) else (annotation,)
    if str in options or any(get_origin(option) is Literal and
                             all(isinstance(v, str) for v in get_args(option)) for option in options):
        return text
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ParameterError("non-text CLI parameters require a JSON value") from exc


@dataclass(frozen=True)
class ParameterSchema:
    model: type
    fields: tuple[ParameterField, ...]
    label: str

    @classmethod
    def compile(cls, model, label):
        try:
            schema = cls(model, _model_fields(model), label)
            # Validate constructor/post-init behavior too whenever all defaults exist.
            if all(item.default is not MISSING for item in schema.fields):
                schema.parse()
            return schema
        except (TypeError, ValueError, NameError) as exc:
            raise ParameterError(f"{label}: invalid parameter schema: {exc}") from exc

    def parse(self, values=None, *, cli=False):
        try:
            if values is None:
                supplied = {}
            elif type(values) is self.model:
                if cli:
                    raise ParameterError("CLI parameters must be a mapping")
                supplied = {item.name: getattr(values, item.name) for item in self.fields}
            elif isinstance(values, Mapping):
                supplied = dict(values)
            else:
                raise ParameterError(f"expected mapping or {self.model.__name__}")
            names = {item.name for item in self.fields}
            unknown = set(supplied) - names
            if unknown:
                raise ParameterError(f"unknown parameter(s): {', '.join(sorted(map(str, unknown)))}")
            parsed = {}
            for item in self.fields:
                if item.name in supplied:
                    value = supplied[item.name]
                    if cli:
                        try:
                            value = _cli_value(item.annotation, value)
                        except ParameterError as exc:
                            raise ParameterError(f"{item.name}: {exc}") from exc
                elif item.default is MISSING:
                    raise ParameterError(f"missing required parameter: {item.name}")
                else:
                    value = item.default
                parsed[item.name] = _validate(item.annotation, value, item.name)
            if type(values) is self.model:
                # Revalidation must not rerun post-init transformations of reviewed input.
                result = object.__new__(self.model)
                for name, value in parsed.items():
                    object.__setattr__(result, name, value)
            else:
                result = self.model(**parsed)
            for item in self.fields:
                value = _validate(item.annotation, getattr(result, item.name), item.name)
                object.__setattr__(result, item.name, value)
            return result
        except (TypeError, ValueError) as exc:
            raise ParameterError(f"{self.label}: {exc}") from exc
