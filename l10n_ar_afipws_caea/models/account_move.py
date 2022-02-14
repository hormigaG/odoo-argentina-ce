from odoo import fields, models, _
from odoo.exceptions import UserError
from datetime import datetime
import sys
import traceback
import logging
_logger = logging.getLogger(__name__)

try:
    from pysimplesoap.client import SoapFault
except ImportError:
    _logger.debug('Can not `from pyafipws.soap import SoapFault`.')


class AccountMove(models.Model):
    _inherit = 'account.move'

    caea_id = fields.Many2one(
        'afipws.caea',
        string='Caea',
        copy=False
    )
    caea_post_datetime = fields.Datetime(
        string='CAEA post datetime',
    )
    l10n_ar_afip_caea_reported = fields.Boolean(
        string='Caea Reported',
    )

    def get_pyafipws_last_invoice(self, document_type):
        if self.journal_id.l10n_ar_afip_pos_system == 'CAEA':
            return self._l10n_ar_get_document_number_parts(self.l10n_latam_document_number,
                                                           self.l10n_latam_document_type_id.code)['invoice_number']
        else:
            return super().get_pyafipws_last_invoice(document_type)

    def post(self):
        caea_state = self.env['ir.config_parameter'].sudo().get_param(
            'afip.ws.caea.state', 'inactive')
        if caea_state == 'active':
            inv_ids = self.filtered(
                lambda record: record.journal_id.l10n_ar_afip_pos_system != 'CAEA')
            for inv in inv_ids:
                if len(inv.journal_id.caea_journal_id):
                    inv.journal_id = inv.journal_id.caea_journal_id.id

        res = super().post()
        return res

    def do_pyafipws_request_cae(self):
        caea_state = self.env['ir.config_parameter'].sudo().get_param(
            'afip.ws.caea.state', 'inactive')
        if caea_state == 'inactive':
            return super().do_pyafipws_request_cae()
        elif caea_state == 'active':
            return self.do_pyafipws_request_caea()

    def do_pyafipws_request_caea(self):
        for inv in self:
            if inv.journal_id.l10n_ar_afip_pos_system != 'CAEA':
                continue
            # Ignore invoices with cae (do not check date)
            if inv.afip_auth_code:
                continue

            afip_ws = inv.journal_id.afip_ws
            if not afip_ws:
                continue

            # Ignore invoice if not ws on point of sale
            if not afip_ws:
                raise UserError(_(
                    'If you use electronic journals (invoice id %s) you need '
                    'configure AFIP WS on the journal') % (inv.id))

            active_caea = inv.company_id.get_active_caea()
            if len(active_caea):
                msg = (
                    _('Afip conditional validation (CAEA %s)') % active_caea.name)
                inv.write({
                    'afip_auth_mode': 'CAEA',
                    'afip_auth_code': active_caea.name,
                    'afip_auth_code_due': inv.invoice_date,
                    'afip_result': '',
                    'afip_message': msg,
                    'caea_post_datetime': fields.Datetime.now(),
                    'caea_id': active_caea.id
                })
                inv.message_post(body=msg)
                continue
            else:
                raise UserError(_('The company does not have active CAEA'))

    def do_pyafipws_post_caea_invoice(self):
        "Request to AFIP the invoices' Authorization Electronic Code (CAE)"
        for inv in self:
            # Ignore invoices with cae (do not check date)
            #

            if inv.afip_auth_code and inv.afip_auth_mode != 'CAEA':
                continue
            afip_ws = inv.journal_id.afip_ws
            if not afip_ws:
                continue

            # Ignore invoice if not ws on point of sale
            if not afip_ws:
                raise UserError(_(
                    'If you use electronic journals (invoice id %s) you need '
                    'configure AFIP WS on the journal') % (inv.id))

            # get the electronic invoice type, point of sale and afip_ws:
            commercial_partner = inv.commercial_partner_id
            country = commercial_partner.country_id
            journal = inv.journal_id
            pos_number = journal.l10n_ar_afip_pos_number
            doc_afip_code = inv.l10n_latam_document_type_id.code

            # authenticate against AFIP:
            ws = inv.company_id.get_connection(afip_ws).connect()

            partner_id_code = commercial_partner.l10n_latam_identification_type_id.l10n_ar_afip_code
            tipo_doc = partner_id_code or '99'
            nro_doc = \
                partner_id_code and commercial_partner.vat or "0"

            cbt_desde = cbt_hasta = cbte_nro = inv._l10n_ar_get_document_number_parts(inv.l10n_latam_document_number,
                                                                                      inv.l10n_latam_document_type_id.code)['invoice_number']
            concepto = tipo_expo = int(inv.l10n_ar_afip_concept)

            fecha_cbte = inv.invoice_date.strftime('%Y%m%d')

            mipyme_fce = int(doc_afip_code) in [201, 206, 211]
            # due date only for concept "services" and mipyme_fce
            if int(concepto) != 1 and int(doc_afip_code) not in [202, 203, 207, 208, 212, 213] or mipyme_fce:
                fecha_venc_pago = inv.invoice_date_due or inv.invoice_date
                if afip_ws != 'wsmtxca':
                    fecha_venc_pago = fecha_venc_pago.strftime('%Y%m%d')
            else:
                fecha_venc_pago = None

            # fecha de servicio solo si no es 1
            if int(concepto) != 1:
                fecha_serv_desde = inv.l10n_ar_afip_service_start
                fecha_serv_hasta = inv.l10n_ar_afip_service_end
            else:
                fecha_serv_desde = fecha_serv_hasta = None

            amounts = self._l10n_ar_get_amounts()
            # invoice amount totals:
            imp_total = str("%.2f" % inv.amount_total)
            # ImpTotConc es el iva no gravado
            imp_tot_conc = str("%.2f" % amounts['vat_untaxed_base_amount'])
            # tal vez haya una mejor forma, la idea es que para facturas c
            # no se pasa iva. Probamos hacer que vat_taxable_amount
            # incorpore a los imp cod 0, pero en ese caso termina reportando
            # iva y no lo queremos
            if inv.l10n_latam_document_type_id.l10n_ar_letter == 'C':
                imp_neto = str("%.2f" % inv.amount_untaxed)
            else:
                imp_neto = str("%.2f" % amounts['vat_taxable_amount'])
            imp_iva = str("%.2f" % amounts['vat_amount'])
            imp_trib = str("%.2f" % amounts['not_vat_taxes_amount'])
            imp_op_ex = str("%.2f" % amounts['vat_exempt_base_amount'])
            moneda_id = inv.currency_id.l10n_ar_afip_code
            moneda_ctz = inv.l10n_ar_currency_rate

            CbteAsoc = inv.get_related_invoices_data()

            # create the invoice internally in the helper
            if afip_ws == 'wsfe' and inv.afip_auth_mode == 'CAEA' and inv.afip_auth_code:
                caea = inv.afip_auth_code
                CbteFchHsGen = inv.caea_post_datetime.strftime('%Y%m%d%H%M%S')

                ws.CrearFactura(
                    concepto, tipo_doc, nro_doc, doc_afip_code, pos_number,
                    cbt_desde, cbt_hasta, imp_total, imp_tot_conc, imp_neto,
                    imp_iva,
                    imp_trib, imp_op_ex, fecha_cbte, fecha_venc_pago,
                    fecha_serv_desde, fecha_serv_hasta,
                    moneda_id, moneda_ctz, caea, CbteFchHsGen
                )

                if mipyme_fce:
                    # agregamos cbu para factura de credito electronica
                    ws.AgregarOpcional(
                        opcional_id=2101,
                        valor=inv.invoice_partner_bank_id.acc_number)
                    # agregamos tipo de transmision si esta definido
                    transmission_type = self.env['ir.config_parameter'].sudo().get_param(
                        'l10n_ar_afipws_fe.fce_transmission', '')
                    if transmission_type:
                        ws.AgregarOpcional(
                            opcional_id=27,
                            valor=transmission_type)
                elif int(doc_afip_code) in [202, 203, 207, 208, 212, 213]:
                    valor = inv.afip_fce_es_anulacion and 'S' or 'N'
                    ws.AgregarOpcional(
                        opcional_id=22,
                        valor=valor)

                not_vat_taxes = self.line_ids.filtered(
                    lambda x: x.tax_line_id and x.tax_line_id.tax_group_id.l10n_ar_tribute_afip_code)
                for tax in not_vat_taxes:
                    ws.AgregarTributo(
                        tax.tax_line_id.tax_group_id.l10n_ar_tribute_afip_code,
                        tax.tax_line_id.tax_group_id.name,
                        "%.2f" % sum(self.invoice_line_ids.filtered(lambda x: x.tax_ids.filtered(
                            lambda y: y.tax_group_id.l10n_ar_tribute_afip_code ==
                            tax.tax_line_id.tax_group_id.l10n_ar_tribute_afip_code)).mapped('price_subtotal')),
                        # "%.2f" % abs(tax.base_amount),
                        # TODO pasar la alicuota
                        # como no tenemos la alicuota pasamos cero, en v9
                        # podremos pasar la alicuota
                        0,
                        "%.2f" % tax.price_subtotal,
                    )

            if CbteAsoc:
                # fex no acepta fecha
                doc_number_parts = self._l10n_ar_get_document_number_parts(
                    CbteAsoc.l10n_latam_document_number, CbteAsoc.l10n_latam_document_type_id.code)
                if afip_ws == 'wsfex':
                    ws.AgregarCmpAsoc(
                        CbteAsoc.l10n_latam_document_type_id.document_type_id.code,
                        doc_number_parts['point_of_sale'],
                        doc_number_parts['invoice_number'],
                        self.company_id.vat,
                    )
                else:
                    ws.AgregarCmpAsoc(
                        CbteAsoc.l10n_latam_document_type_id.code,
                        doc_number_parts['point_of_sale'],
                        doc_number_parts['invoice_number'],
                        self.company_id.vat,
                        afip_ws != 'wsmtxca' and CbteAsoc.invoice_date.strftime(
                            '%Y%m%d') or CbteAsoc.invoice_date.strftime('%Y-%m-%d'),
                    )



            # Request the authorization! (call the AFIP webservice method)
            vto = None
            msg = False
            try:
                afip_auth_code = ws.CAEARegInformativo()
                vto = ws.Vencimiento

            except SoapFault as fault:
                msg = 'Falla SOAP %s: %s' % (
                    fault.faultcode, fault.faultstring)
            except Exception as e:
                msg = e
            except Exception:
                if ws.Excepcion:
                    # get the exception already parsed by the helper
                    msg = ws.Excepcion
                else:
                    # avoid encoding problem when   raising error
                    msg = traceback.format_exception_only(
                        sys.exc_type,
                        sys.exc_value)[0]

            if msg:
                _logger.info(_('AFIP Validation Error. %s' % msg) + ' XML Request: %s XML Response: %s' % (
                    ws.XmlRequest, ws.XmlResponse))
                raise UserError(_('AFIP Validation Error. %s' % msg))

            msg = u"\n".join([ws.Obs or "", ws.ErrMsg or ""])
            if not ws.CAEA or ws.Resultado != 'A':

                raise UserError(_('AFIP Validation Error. %s' % msg))
            # TODO ver que algunso campos no tienen sentido porque solo se
            # escribe aca si no hay errores
            if vto:
                vto = datetime.strptime(vto, '%Y%m%d').date()
            _logger.info('CAEA solicitado con exito. %s. Resultado %s' % (afip_auth_code, ws.Resultado))
            inv.write({
                'afip_auth_mode': ws.EmisionTipo,
                'afip_auth_code': afip_auth_code,
                'afip_auth_code_due': vto,
                'afip_result': ws.Resultado,
                'afip_message': msg,
                'afip_xml_request': ws.XmlRequest,
                'afip_xml_response': ws.XmlResponse,
                'l10n_ar_afip_caea_reported': True
            })
            # si obtuvimos el caea hacemos el commit porque estoya no se puede
            # volver atras
            # otra alternativa seria escribir con otro cursor el cae y que
            # la factura no quede validada total si tiene cae no se vuelve a
            # solicitar. Lo mismo podriamos usar para grabar los mensajes de
            # afip de respuesta
            inv._cr.commit()
