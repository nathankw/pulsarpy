#!/usr/bin/env python3

###
#Nathaniel Watson
#Stanford School of Medicine
#Nov. 6, 2018
#nathankw@stanford.edu
###

"""
Accepts DNAnexus projects pending transfer to the ENCODE org, then downloads each of the projects to the 
local host at the designated output directory. In DNAnexus, a project property will be added to the 
project; this property is 'scHub' and will be set to True to indicate that the project was 
downloaded to the SCHub pod. Project downloading is handled by the script download_cirm_dx-project.py,
which sends out notification emails as specified in the configuration file {} in both successful 
and unsuccessful circomstances.".format(conf_file). See more details at 
https://docs.google.com/document/d/1ykBa2D7kCihzIdixiOFJiSSLqhISSlqiKGMurpW5A6s/edit?usp=sharing 
and https://docs.google.com/a/stanford.edu/document/d/1AxEqCr4dWyEPBfp2r8SMtz8YE_tTTme730LsT_3URdY/edit?usp=sharing.
"""

import os
import sys
import subprocess
import logging
import argparse
import json

import dxpy

import pulsarpy.models
import scgpm_seqresults_dnanexus.dnanexus_utils as du


#The environment module gbsc/gbsc_dnanexus/current should also be loaded in order to log into DNAnexus

ENCODE_ORG = "org-snyder_encode"


def get_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    return parser

def main():
    get_parser()
    #parser.parse_args()
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s:%(name)s:%(levelname)s:   %(message)s')
    chandler = logging.StreamHandler(sys.stdout)
    chandler.setLevel(logging.DEBUG)
    chandler.setFormatter(formatter)
    logger.addHandler(chandler)
    
    # Add debug file handler
    fhandler = logging.FileHandler(filename="log_debug_dx-seq-import.txt",mode="a")
    fhandler.setLevel(logging.DEBUG)
    fhandler.setFormatter(formatter)
    logger.addHandler(fhandler)

    # Add error file handler
    err_h = logging.FileHandler(filename="log_error_dx-seq-import.txt" ,mode="a")
    err_h.setLevel(logging.ERROR)
    err_h.setFormatter(formatter)
    logger.addHandler(err_h)

    #accept pending transfers
    transferred = du.accept_project_transfers(dx_username=DX_USER,access_level="ADMINISTER",queue="ENCODE",org=ENCODE_ORG,share_with_org="CONTRIBUTE")
    #transferred is a dict. identifying the projects that were transferred to the specified billing account. Keys are the project IDs, and values are the project names.
    logger.debug("The following projects were transferred to {org}:".format(org=ENCODE_ORG))
    logger.debug(transferred)
    
    if not transferred: #will be an empty dict otherwise.
        return
    transferred_proj_ids = transferred.keys()
    for t in transferred_proj_ids:
        dxres = du.DxSeqResults(dx_project_id=t)
        proj_props = dxres.dx_project.describe(input_params={"properties": True})["properties"]
        lib_name_prop = proj_props["library_name"]
        # First search by name, then by ID if the former fails.
        # Lab members submit a name by the name of SREQ-ID, where SREQ is Pulsar's 
        # abbreviation for the SequencingRequest model, and ID is the database ID of a
        # SequencingRequest record. This gets stored into the library_name property of the 
        # corresponding DNanexus project. Problematically, this was also done in the same way when
        # we were on Syapse, and we have backported some Sypase sequencing requests into Pulsar. Such
        # SequencingRequests have been given the name as submitted in Syapse times, and this is
        # evident when the SequencingRequest's ID is different from the ID in the SREQ-ID part. 
        # Find pulsar SequencingRequest:
        sreq = ppy_models.SequencingRequest(library_name})
        if not sreq:
            # Search by ID. The lab sometimes doesn't add a value for SequencingRequest.name.
            sreq = ppy_models.SequencingRequest(library_name.split("-")[1])
        if not sreq:
            logger.debug("Can't find Pulsar SequencingRequest for DNAnexus project {} ({}).".format(t, dxres.name))
            continue
        check_pairedend_correct(sreq, dxres.dx_project_properties["paired_end"])
        seq_run_name = proj_props["seq_run_name"]
        srun = get_or_create_srun(sreq, seq_run_name, dxres)
        # Check if DataStorage is aleady linked to SequencingRun object. May be if user created it
        # manually in the past. 
        if not srun.data_storage_id:
            ds_json = create_data_storage(srun, dxres)
        if srun.status != "finished":
            srun.patch({"status": "finished"})
        
        # Create SequencingResult record for each library on the SReq
        for library_id in sreq.library_ids:
            library = models.Library(library_id)
            barcode = library.get_barcode_sequence()
            # Find the barcode file on DNAnexus
            barcode_files = dxres.get_fastq_files_props(barcode=barcode)
            # Above - keys are the FASTQ file DXFile objects; values are the dict of associated properties 
            # on DNAnexus on the file. In addition to the properties on the file in DNAnexus, an 
            # additional property is added here called 'fastq_file_name'.

            # Download sample_stats.json to get mapped read counts
            sample_stats = dxres.get_sample_stats_json(barcode=barcode)
            for dxfile in barcode_files:
                props = barcode_files[dxfile]
                read_num = int(props["read"])
                if not read_num in [1, 2]:
                    raise Exception("Unknown read number '{}'. Should be either 1 or 2.".format(read_num))
                payload = {}
                payload["library_id"] = library_id
                payload["sequencing_run_id"] = srun.id
                
                if read_num == 1:
                    payload["read1_uri"] = dxfile.project + ":" + dxfile.id   
                    read_stats = get_read_stats(sample_stats, read_num=1)
                    payload["read1_count"] = read_counts["pass_filter"]
                else:
                    payload["read2_uri"] = dxfile.project + ":" + dxfile.id   
                    read_stats = get_read_stats(sample_stats, read_num=2)
                    payload["read2_count"] = read_counts["pass_filter"]
                models.SequencingResult.post(payload)

def check_pairedend_correct(sreq, dx_pe_val):
    """
    Checks whether the SequencingRequest.paired_end attribute and the 'paired' property of the 
    DNAnexus project in question are in accordance. It's possible that the request originally went
    in as SE (or the tech forgot to check PE), but the sequencing run was acutally done PE. If this
    is the case, then the SequencingRequest.paired_end attribute will be set to True in order that
    PE sequencing results will be allowed (PE attributes of a SequencingResult will be hidden in the
    UI if the SequencingRequest is set to paired_end being false).  

    Args:
        sreq: A `pulsarpy.models.SequencingRequest` instance.
        dx_pe_val: `str`. The value of the 'paired' property of the DNAnexus project in questions. 
    """
    if sreq.paired_end == False:
        if dx_pe_val == "true":
            sreq.patch({"paired_end": True})
                
def get_read_stats(sample_stats, read_num):
    """
    Each DNAnexus project from GSSC contains a sample_stats.json file that has read stats.
    This function accepts a barcode-specific hash from that file and parses out some useful
    read-based stats for the given read number. An example of a sample_stats.json file is provided
    in the data subdirectory of this script.

    Args:
        sample_stats: `dict`. The sample stats dict for a specific barcode parsed directly out of
            the sample_stats.json file in the relevant DNAnexus project. See 
            `scgpm_seqresults_dnanexus.dnanexus_utils.DxSeqResults.get_sample_stats_json()` for
            more details.
        read_num: `int`. The read number (1 or 2) for which you need read stats. 
    """
    read_stats = {}
    read_num_key = "Read {}".format(read_num)
    read_stats["pass_filter"] = sample_stats[read_num_key]["Post-Filter Reads"]
    return read_stats

def get_or_create_srun(sreq, seq_run_name, dxres):
    """
    Given a SequencingRequest record, checks to see if it has any SequencingRequests associated with
    a name equal to the sequencing run name. If so, returns it, otherwise, creates a new sequencing
    run record based off of the provided DNAnexus sequencing results. 

    Args:
        sreq: `pulsarpy.models.SequencingRequest` instance.
        seq_run_name: `str`. Typically, the value of a DNAnexus project property called "seq_run_name". 
        dxres - `scgpm_seqresults_dnanexus.dnanexus_utils.du.DxSeqResults()` instance that contains
               sequencing results metadata in DNAnexus that represents a sequencing run of the given
               `pulsarpy.models.SequencingRequest`.
    Returns:
        `pulsarpy.models.SequencingRun` instance. 
    """
    srun_ids = sreq.sequencing_run_ids
    if srun_ids:
        for i in srun_ids:
            srun = models.SequencingRun(i)
            # Check by name
            if srun.name == seq_run_name:
                return srun
            # Also check by DataStorage
            elif srun.data_storage_id:
                ds = models.DataStorage(srun.data_storage_id)
                if ds.project_identifier == dxres.dx_project_id:
                    return srun
    # Create SequencingRun
    srun_json = create_srun(sreq, dxres)
    srun = models.SequencingRun(srun_json["id"])
    return srun

def create_srun(sreq, dxres):
    """
    Creates a SequencingRun record based on the provided DNAnexus sequencing results, to be linked
    to the given SequencingRequest object. 
    """
    payload = {}
    payload["name"] = proj_props["seq_run_name"]
    payload["sequencing_request_id"] = sreq.id
    payload["status"] = "started"
    # 'status' is a required attribute. Set initially to 'started'; it will be set to finished
    # a step later when creating the associated DataStorage record.
    return models.SequencingRun.post(payload)
        
def create_data_storage(srun, dxres):
    """
    Creates a DataStorage record for the given SequencingRun record based on the given DNAnexus 
    sequencing results. After the DataStorage record is created, a few attribuets of the SequencingRun
    object will then be set:

        1. `SequencingRun.data_storage_id`: Link to newly creatd DataStroage record.
        2. `SequencingRun.lane`: Set to the value of the DNAnexus project property "seq_lane_index". 
        3. `SequencingRun.status`: Set to "finished". 


     Note that I would also like to try and set the attributes `SequencingRun.forward_read_len` and
     `SequencingRun.reverse_read_len`, however, I can't obtain these results from DNAnexus based on
     the existing metadata that's sent there via GSSC. 

    key in the SequeningRun record. 

    Args: 
        srun: `pulsarpy.models.SequencingRun` instance whose `data_storage_id` foreign key 
               should be associated with the newly created DataStorage.
        dxres: `scgpm_seqresults_dnanexus.dnanexus_utils.du.DxSeqResults()` instance that contains
               sequencing results metadata in DNAnexus for the given srun. 
    
    Returns:
        `dict`. The response from the server containing the JSON serialization of the new 
            DataStorage record. 
    """
    payload = {}
    payload["name"] = dxres.dx_project_name
    payload["project_identifier"] = dxres.dx_project_id
    payload["data_storage_provider_id"] = models.DataStorage("DNAnexus")["id"]
    # Create DataStorage
    res_json = models.DataStorage.post(ds_payload)
    # Udate srun's data_storage_id fkey:
    srun.patch({"data_storage_id": res_json["id"], "lane": dxres.dx_project_props["seq_lane_index"]})
    return res_json

if __name__ == "__main__":
    main()