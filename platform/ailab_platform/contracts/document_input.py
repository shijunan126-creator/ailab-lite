"""New Chat document operations; frozen legacy run contracts live separately."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Pages(Contract):
    kind: Literal["pages"]
    pages: list[Annotated[StrictInt, Field(ge=1, le=2147483647)]] = Field(min_length=1, max_length=20)

    @field_validator("pages")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("duplicate pages")
        return sorted(value)


class Word(Contract):
    kind: Literal["word"]
    anchor: str = Field(pattern=r"^(p:[1-9][0-9]*|t:[1-9][0-9]*(/r[1-9][0-9]*c[1-9][0-9]*)?)$", max_length=100)


class Sheet(Contract):
    kind: Literal["sheet"]
    sheet: str = Field(min_length=1, max_length=64)
    range: str | None = Field(default=None, pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}(:[A-Z]{1,3}[1-9][0-9]{0,6})?$", max_length=40)

    @field_validator("range")
    @classmethod
    def bounds(cls, value):
        if value:
            import re

            positions = []
            for cell in value.split(":"):
                letters, row = re.fullmatch(r"([A-Z]+)([0-9]+)", cell).groups()
                col = 0
                for letter in letters:
                    col = col * 26 + ord(letter) - 64
                if col > 16384 or int(row) > 1048576:
                    raise ValueError("cell outside worksheet")
                positions.append((int(row), col))
            if len(positions) == 2 and (positions[1][0] < positions[0][0] or positions[1][1] < positions[0][1]):
                raise ValueError("reversed range")
        return value


Selector = Annotated[Pages | Word | Sheet, Field(discriminator="kind")]


class Region(Contract):
    kind: Literal["region"]
    page: StrictInt | None = Field(default=None, ge=1, le=2147483647)
    image_id: str | None = Field(default=None, min_length=1, max_length=100)
    # Coordinates refer to the stated representation, never inferred Word pages.
    revision: str = Field(min_length=1, max_length=100)
    rotation: Literal[0, 90, 180, 270] = 0
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside(self):
        if bool(self.page) == bool(self.image_id):
            raise ValueError("region requires exactly one page or image_id")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("region outside representation")
        return self


class Image(Contract):
    kind: Literal["image"]
    image_id: str = Field(min_length=1, max_length=100)
    revision: str = Field(min_length=1, max_length=100)


# One tagged union so an invalid selector reports the fields of its own kind only.
TextSelector = Annotated[Pages | Word | Sheet | Region, Field(discriminator="kind")]


class ReadDocument(Contract):
    file_id: str = Field(min_length=1, max_length=40)
    selector: TextSelector | None = None
    text_source: Literal["native", "ocr"] = "native"
    navigation_only: bool = False
    cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def explicit_ocr(self):
        if self.navigation_only and self.text_source != "native":
            raise ValueError("navigation uses native structure, not OCR")
        if self.text_source == "ocr" and not isinstance(self.selector, Pages | Region):
            raise ValueError("OCR requires explicit pages or region")
        if self.text_source == "native" and isinstance(self.selector, Region):
            raise ValueError("native text uses structure selectors")
        return self


class SearchDocument(Contract):
    file_id: str = Field(min_length=1, max_length=40)
    query: str = Field(min_length=1, max_length=200)
    selector: TextSelector | None = None
    cursor: str | None = Field(default=None, max_length=2048)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("empty query")
        return value


class SheetView(Contract):
    """A worksheet's rendered pages; cells are read, not viewed, so no range."""

    kind: Literal["sheet"]
    sheet: str = Field(min_length=1, max_length=64)


class ViewDocument(Contract):
    file_id: str = Field(min_length=1, max_length=40)
    target: Annotated[Pages | SheetView | Region | Image, Field(discriminator="kind")]
    cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def batch(self):
        if isinstance(self.target, Pages) and len(self.target.pages) > 4:
            raise ValueError("view supports at most four pages")
        return self
