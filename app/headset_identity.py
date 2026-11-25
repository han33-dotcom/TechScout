"""Manufacturer configuration references for wired headsets with independently checked hardware.

A local SKU, catalog slug or bare model name alone cannot identify a cross-store variant.
No merchant, price, stock, URL for sale or destination is part of the reference.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class HeadsetConfiguration:
    maker: str
    model: str
    color: str
    connector: str
    part: str
    manufacturer_url: str

    @property
    def product_id(self):
        return f'audio:{self.maker.casefold()}:{self.model.casefold()}'

    @property
    def variant_id(self):
        return f'{self.product_id}:configuration:{self.color}:{self.connector}'


CONFIGURATIONS = (
    HeadsetConfiguration('Logitech', 'H340', 'black', 'usb-a', '981-000475',
        'https://www.logitech.com/en-ie/shop/p/h340-usb-pc-noise-cancelling-mic.981-000475'),
)


def identify_headset(maker, model, color, *, connector=None, part=None):
    """Require matching independent detail in addition to exact manufacturer, model and color."""
    matches = [item for item in CONFIGURATIONS
               if (maker.casefold(), model.casefold(), color.casefold()) ==
                  (item.maker.casefold(), item.model.casefold(), item.color)
               and (connector or part)
               and (connector is None or connector == item.connector)
               and (part is None or part == item.part)]
    return matches[0] if len(matches) == 1 else None
