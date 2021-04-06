##############################################################################
# For copyright and license notices, see __manifest__.py file in module root
# directory
##############################################################################

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    mipyme_required = fields.Boolean(
        string='mipyme required',
    )
    mipyme_from_amount = fields.Float(
        string='from amount',
    )

    def l10n_ar_afipws_fe_min_ammount(self):
        for record in self:
            if record.l10n_ar_vat:
                ws = self.env.user.company_id.get_connection('wsfecred').connect()
                res = ws.ConsultarMontoObligadoRecepcion(record.l10n_ar_vat)
                record.mipyme_required = True if ws.Resultado == 'S' else False
                record.mipyme_from_amount = float(res)
