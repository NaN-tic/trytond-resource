# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import Pool
from resource import *


def register():
    Pool.register(
        Company,
        ResourceConfiguration,
        Resource,
        ResourceConfigIrModel,
        ResourceBooking,
        module='resource', type_='model')
