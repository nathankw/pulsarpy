# -*- coding: utf-8 -*-

###
# © 2018 The Board of Trustees of the Leland Stanford Junior University
# Nathaniel Watson
# nathankw@stanford.edu
###

"""
Required Environment Variables
  1) Those that are required in the pulsarpy.models module to submit data to the ENCODE Portal::
     -PULSAR_API_URL and PULSAR_TOKEN
  2) Those that are required in the encode_utils.connection module to read data out of Pulsar:
     -DCC_API_KEY and DCC_SECRET_KEY
"""

import base64

from pulsarpy import models
import encode_utils as eu
import encode_utils.connection as euc
import pdb


class Submit():
    UPSTREAM_ATTR = "upstream_identifier"

    def __init__(self, dcc_mode=None):
        if not dcc_mode:
            try:                                                                                    
                dcc_mode = os.environ["DCC_MODE"]                                                   
                print("Utilizing DCC_MODE environment variable.")                 
            except KeyError:                                                                        
                print("ERROR: You must supply the `dcc_mode` argument or set the environment variable DCC_MODE.")
                sys.exit(-1)                                                                        
        self.dcc_mode = dcc_mode
        self.ENC_CONN = euc.Connection(self.dcc_mode)
    
    def filter_standard_attrs(self, payload):
        attrs = ["created_at", "id", "owner_id", "updated_at", "user_id"]
        for i in attrs:
            if i in payload:
                payload.pop(i)
        for i in payload:
            if i.startswith("_"):
                payload.pop(i)
        return payload
    
    def patch(self, upstream, payload, raise_403=True, extend_array_values=False):
        """Updates a record in the ENCODE Portal based on its state in Pulsar.
    
        Args:
            payload: `dict`. containing the attribute key and value pairs to patch.
            raise_403: `bool`. `True` means to raise a ``requests.exceptions.HTTPError`` if a 403 status
                (forbidden) is returned.
                If set to `False` and there still is a 403 return status, then the object you were
                trying to PATCH will be fetched from the Portal in JSON format as this function's
                return value.
            extend_array_values: `bool`. Only affects keys with array values. `True` (default) means to
                extend the corresponding value on the Portal with what's specified in the payload.
                `False` means to replace the value on the Portal with what's in the payload.
    
        Returns:
            `dict`. Will be empty if the record to PATCH wasn't found on the ENCODE Portal or if the
            connection object to the ENCODE Portal has the dry-run feature turned on. If the PATCH
            operation returns a 403 Forbidden status and the ignore403 argument is set, then the
            record as it presently exists on the Portal will be returned.
        """
        payload[self.ENC_CONN.ENCID_KEY] = upstream
        res = self.ENC_CONN.patch(payload=payload, raise_403=raise_403, extend_array_values=extend_array_values)
        # res will be {} if record doesn't exist on the ENCODE Portal.
        if not res:
            print("Warning: Could not PATCH '{}' as its upstream identifier was not found on the ENCODE Portal.".format(upstream))
    
    def post(self, payload, dcc_profile, rec_id):
        """
        POSTS a record from Pulsar to the ENCODE Portal and updates the Pulsar record's
        'upstream_identifier' attribute to reference the new object on the Portal.
    
        Args:
            payload: `dict`. The new record attributes to submit.
            dcc_profile: `str`. The name of the ENCODE Profile for this record, i.e. 'biosample',
                'genetic_modification'.
        Returns:
            `str`: The record identifier of the new record on the ENCODE Portal, or the existing
            record identifier if the record already exists.
        """
        # Get a reference to the model class (i.e. Biosample) in the models module
        mod = models.model_class_lookup(rec_id)
        # Make sure the record's UPSTREAM_ATTR isn't set, which would mean that it was already POSTED
        rec = mod.get(rec_id)
        upstream = rec.get(self.UPSTREAM_ATTR)
        if upstream:
            print("Will not POST '{}' since it was already submitted as '{}'.".format(rec_id, upstream))
            return upstream
        payload[self.ENC_CONN.PROFILE_KEY] = dcc_profile
    
        # `dict`. The POST response if the record didn't yet exist on the ENCODE Portal, or the
        # record itself if it does already exist. Note that the dict. will be empty if the connection
        # object to the ENCODE Portal has the dry-run feature turned on.
        response_json = self.ENC_CONN.post(payload)
        if "accession" in response_json:
            upstream = response_json["accession"]
        elif "aliases" in response_json:
            upstream = response_json["aliases"][0]
        elif "uuid" in response_json:
            upstream = response_json["uuid"]
        # Set value of the Pulsar record's upstream_identifier, but only if we are in prod mode since
        # we don't want to set it to an upstream identifiers from any of the ENOCODE Portal  test servers. 
        if self.dcc_mode == eu.DCC_PROD_MODE:
            print("Setting the Pulsar record's upstream_identifier attribute to '{}'.".format(upstream))
            mod.patch(uid=rec_id, payload={"upstream_identifier": upstream})
        return upstream
    
    def post_crispr_modification(self, rec_id, patch=False):
        cm = models.CrisprModification.get(rec_id)
        res = self.post(payload=payload, dcc_profile="genetic_modification", rec_id=rec_id)
    
    def post_document(self, rec_id, patch=False):
        doc = models.Document.get(rec_id)
        # Before having to locally download document, check if upstream_identifier attr. is set.
        upstream = doc[self.UPSTREAM_ATTR]
        if upstream and not patch:
            return upstream
        payload = {}
        payload["aliases"] = [doc["name"]]
        payload["description"] = doc["description"]
        payload["document_type"] = doc["document_type"]["name"]
        content_type = doc["content_type"]
        # Create attachment for the attachment prop
        file_contents = models.Document.download(rec_id)
        data = base64.b64encode(file_contents)
        temp_uri = str(data, "utf-8")
        href = "data:{mime_type};base64,{temp_uri}".format(mime_type=content_type, temp_uri=temp_uri)
        attachment = {}
        attachment["download"] = doc["name"]
        attachment["type"] = content_type 
        attachment["href"] = href
        payload["attachment"] = attachment
        if patch:
            res = self.patch(upstream=upstream,payload=payload)
        else:
            res = self.post(payload=payload, dcc_profile="document", rec_id=rec_id)
        return res
    
    def post_donor(self, rec_id, patch=False):
        don = models.Donor.get(rec_id)
        payload = {}
        res = self.post(payload=payload, dcc_profile="donor", rec_id=rec_id)
    
    def post_vendor(self, rec_id, patch=False):
        """
        Returns:
            `str`: The value
        """
        ven = models.Vendor.get(rec_id)
        payload = {}
        res = self.post(payload=payload, dcc_profile="source", rec_id=rec_id)
    
    def post_biosample(self, rec_id, patch=False):
        b = models.Biosample.get(rec_id)
        payload = {}
        # The alias lab prefixes will be set in the encode_utils package if the DCC_LAB environment
        # variable is set.
        payload["aliases"] = [b["name"], b["tube_label"]]
        payload["biosample_term_name"] = b["biosample_term_name"]["name"]
        payload["biosample_term_id"] = b["biosample_term_name"]["accession"]
        payload["biosample_type"] = b["biosample_type"]["name"]
        payload["culture_harvest_date"] = b["date_biosample_taken"]
        payload["description"] = b["description"]
        payload["lot_id"] = b["lot_identifier"]
        payload["nih_institutional_certification"] = b["nih_institutional_certification"]
        payload["organism"] = "human"
        payload["passage_number"] = b["passage_number"]
        payload["starting_amount"] = b["starting_amount"]
        payload["starting_amount_units"] = b["starting_amount_units"]
        payload["submitter_comments"] = b["submitter_comments"]
        payload["tissue_preservation_method"] = b["tissue_preservation_method"]
        payload["vendor_product_identifier"] = b["vendor_product_identifier"]
    
        crispr_modification = b["crispr_modification"]
        if crispr_modification:
            crispr_mod_upstream = crispr_modification[self.UPSTREAM_ATTR]
            if not crispr_mod_upstream:
                crispr_mod_upstream = post_crispr_modification(crispr_modification)
            payload["genetic_modifications"] = crispr_mod_upstream
    
        documents = b["documents"]
        doc_upstreams = []
        for doc in documents:
            doc_upstream = doc[self.UPSTREAM_ATTR]
            if not doc_upstream:
                doc_upstream = post_document(doc)
            doc_upstreams.append(doc_upstream)
        payload["documents"] = doc_upstreams
    
        donor_upstream = b["donor"][self.UPSTREAM_ATTR]
        if not donor_upstream:
            donor_upstream = post_donor(b["donor"])
        payload["donor"] = donor_upstream
    
    
        part_of_biosample_id = b["part_of_biosample_id"]
        if part_of_biosample_id:
            part_of_biosample = models.Biosample.get(part_of_biosample_id)
            pob_upstream = part_of_biosample[self.UPSTREAM_ATTR]
            if not pob_upstream:
                pob_upstream = post_biosample(part_of_biosample)
            payload["part_of"] = pob_upstream
    
        pooled_from_biosamples = b["pooled_from_biosamples"]
        if pooled_from_biosamples:
            payload["pooled_from"] = []
            for p in pooled_from_biosamples:
                p_upstream = p[self.UPSTREAM_ATTR]
                if not p_upstream:
                    p_upstream = post_biosample(p)
                payload["pooled_from"].append(p_upstream)
    
        vendor_upstream = b["vendor"][self.UPSTREAM_ATTR]
        if not vendor_upstream:
            vendor_upstream = post_vendor(b["vendor"])
        payload["source"] = vendor_upstream
    
        treatments = b["treatments"]
        treat_upstreams = []
        for treat in treatments:
            treat_upstream = treat[self.PSTREAM_ATTR]
            if not treat_upstream:
                treat_upstream = post_treatment(treat)
            treat_upstreams.append(treat_upstream)
        payload["treatments"] = treat_upstreams
    
        res = self.post(payload=payload, dcc_profile="biosample", rec_id=rec_id)
