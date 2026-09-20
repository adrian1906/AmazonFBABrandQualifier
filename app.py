"""
Local web UI for the R&T Brand/Supplier Qualifier.

Run with:
    uv run streamlit run app.py

Design principle: simple, bounded actions (viewing reports, approving one
outreach draft, looking up a single brand or supplier) happen right here.
Long-running, expensive batch operations never run from a button click in
this app - the Command Builder tab generates the exact CLI command for a
human to run in a terminal instead (see gui_command_builder.py). Nothing in
this file ever sends an email - Approve only writes to outbox/, exactly
like the CLI approval gates (approval.py / supplier_approval.py).
"""

import streamlit as st

import gui_actions as actions
import gui_command_builder as cmd
import persistence
import supplier_persistence
from report import format_report
from supplier_report import format_full_supplier_report, format_relationship_detail

st.set_page_config(page_title="R&T Brand/Supplier Qualifier", layout="wide")
st.title("R&T Brand/Supplier Qualifier")

tab_reports, tab_review, tab_lookup, tab_commands = st.tabs(
    ["Reports", "Review Queue", "Single Lookup", "Command Builder"]
)

# ---------------------------------------------------------------------------
# Reports - read-only, no agent calls
# ---------------------------------------------------------------------------
with tab_reports:
    st.subheader("Supplier Qualifier reports")
    batch_ids = actions.list_supplier_batch_ids()
    col1, col2 = st.columns(2)
    with col1:
        chosen_batch = st.selectbox("Supplier batch", ["(all)"] + batch_ids)
    with col2:
        brand_fragment = st.text_input("...or filter by brand name fragment", key="report_brand_fragment")

    if brand_fragment.strip():
        paths = supplier_persistence.find_relationships(brand_fragment=brand_fragment.strip())
        rels = [supplier_persistence.load_relationship(p) for p in paths]
    elif chosen_batch != "(all)":
        rels = supplier_persistence.relationships_for_batch(chosen_batch)
    else:
        rels = actions.list_supplier_relationships()

    if not rels:
        st.info("No supplier relationships found yet - try a Single Lookup, or run a batch (see Command Builder).")
    else:
        st.text(format_full_supplier_report(rels))

    st.divider()
    st.subheader("Brand Qualifier results")
    brand_results = actions.list_brand_results()
    if not brand_results:
        st.info("No saved Brand Qualifier results yet.")
    else:
        for r in brand_results:
            label = f"{r.prospect.company_name} — {r.qualification.recommendation} ({r.qualification.overall_score}/100)"
            with st.expander(label):
                st.text(format_report(r))

# ---------------------------------------------------------------------------
# Review Queue - Approve / Edit / Regenerate / Reject
# ---------------------------------------------------------------------------
with tab_review:
    sub_supplier, sub_brand = st.tabs(["Supplier relationships", "Brand results"])

    with sub_supplier:
        pending = actions.list_pending_supplier_relationships()
        if not pending:
            st.info("Nothing pending review. (Only relationships with drafted outreach and no prior contact show up here.)")
        else:
            labels = [
                f"{r.brand_name} -> {r.assessment.candidate.legal_business_name} "
                f"({r.assessment.recommendation}, {r.assessment.score_breakdown.final_score}/100)"
                for r in pending
            ]
            idx = st.selectbox("Pick a relationship to review", range(len(pending)), format_func=lambda i: labels[i])
            rel = pending[idx]
            st.text(format_relationship_detail(rel))

            if rel.outreach_drafts and rel.manager_decision:
                winner = rel.outreach_drafts[rel.manager_decision.winning_strategy]
                new_subject = st.text_input("Subject", value=winner.subject, key=f"s_subj_{idx}")
                new_body = st.text_area("Body", value=winner.body, height=280, key=f"s_body_{idx}")

                c1, c2, c3, c4 = st.columns(4)
                if c1.button("Approve", key=f"s_approve_{idx}", type="primary"):
                    winner.subject, winner.body = new_subject, new_body
                    path = actions.approve_supplier_relationship(rel)
                    st.success(f"Approved - saved to {path} (NOT sent). Send it yourself when ready.")
                    st.rerun()
                if c2.button("Save edits", key=f"s_save_{idx}"):
                    winner.subject, winner.body = new_subject, new_body
                    supplier_persistence.save_relationship(rel)
                    st.success("Edits saved.")
                if c3.button("Regenerate", key=f"s_regen_{idx}"):
                    with st.spinner("Regenerating outreach drafts and manager evaluation..."):
                        actions.regenerate_supplier_relationship(rel)
                    st.success("Regenerated.")
                    st.rerun()
                if c4.button("Reject", key=f"s_reject_{idx}"):
                    st.info("Rejected - nothing was saved.")
            else:
                st.info(f"No outreach was drafted (recommendation: {rel.assessment.recommendation}).")

    with sub_brand:
        results = actions.list_brand_results()
        if not results:
            st.info("No saved Brand Qualifier results yet.")
        else:
            labels = [f"{r.prospect.company_name} ({r.qualification.recommendation}, {r.qualification.overall_score}/100)" for r in results]
            idx = st.selectbox("Pick a brand result to review", range(len(results)), format_func=lambda i: labels[i], key="brand_review_select")
            result = results[idx]
            st.text(format_report(result))

            winner = result.winning_draft
            new_subject = st.text_input("Subject", value=winner.subject, key=f"b_subj_{idx}")
            new_body = st.text_area("Body", value=winner.body, height=280, key=f"b_body_{idx}")

            c1, c2, c3, c4 = st.columns(4)
            if c1.button("Approve", key=f"b_approve_{idx}", type="primary"):
                winner.subject, winner.body = new_subject, new_body
                path = actions.approve_brand_outreach(result)
                st.success(f"Approved - saved to {path} (NOT sent). Send it yourself when ready.")
            if c2.button("Save edits", key=f"b_save_{idx}"):
                winner.subject, winner.body = new_subject, new_body
                persistence.save_result(result)
                st.success("Edits saved.")
            if c3.button("Regenerate", key=f"b_regen_{idx}"):
                with st.spinner("Regenerating outreach drafts and manager evaluation..."):
                    actions.regenerate_brand_outreach(result)
                st.success("Regenerated.")
                st.rerun()
            if c4.button("Reject", key=f"b_reject_{idx}"):
                st.info("Rejected - nothing was saved.")

# ---------------------------------------------------------------------------
# Single Lookup - one brand or one supplier-research pass, bounded cost
# ---------------------------------------------------------------------------
with tab_lookup:
    st.caption("Each lookup here is one brand at a time - a handful of API calls, not a batch. "
               "For many brands at once, use the Command Builder tab instead.")
    sub_b, sub_s = st.tabs(["Brand lookup", "Supplier lookup"])

    with sub_b:
        company = st.text_input("Company/brand name")
        website = st.text_input("Website (optional)")
        notes = st.text_area("Notes you already know (optional)", key="brand_notes")
        live = st.checkbox("Use live web search (costs API credits)", value=True, key="brand_live")
        if st.button("Run Brand Qualifier lookup", disabled=not company.strip()):
            with st.spinner(f"Researching {company}..."):
                result = actions.run_single_brand_lookup(company, website, notes, live)
            st.success("Done - saved. Also visible in the Review Queue tab.")
            st.text(format_report(result))

    with sub_s:
        brand = st.text_input("Brand name", key="s_brand")
        context = st.text_area("Context notes (optional)", key="s_context")
        live2 = st.checkbox("Use live web search (costs API credits)", value=True, key="supplier_live")
        if st.button("Run Supplier Qualifier lookup", disabled=not brand.strip()):
            with st.spinner(f"Researching suppliers for {brand}..."):
                result = actions.run_single_supplier_lookup(brand, context, live2)
            st.success(f"Found {len(result.relationships)} candidate(s) - saved. Also visible in the Review Queue tab.")
            for rel in result.relationships:
                st.text(format_relationship_detail(rel))

# ---------------------------------------------------------------------------
# Command Builder - generates CLI commands; never executes anything itself
# ---------------------------------------------------------------------------
with tab_commands:
    st.caption("These commands are never run automatically by this app - copy the box into your terminal.")
    sub1, sub2, sub3, sub4 = st.tabs(["Brand batch", "Supplier batch", "Resume / Reports", "Review from terminal"])

    with sub1:
        csv_path = st.text_input("CSV path", value="sample_smartscout_export.csv", key="bb_csv")
        limit = st.number_input("Limit (0 = no limit)", min_value=0, value=0, step=1, key="bb_limit")
        concurrency = st.number_input("Concurrency", min_value=1, value=5, step=1, key="bb_conc")
        no_web = st.checkbox("Disable web search (--no-web-search)", key="bb_noweb")
        command, explanation = cmd.brand_batch_command(csv_path, limit or None, concurrency, no_web)
        st.code(command, language="bash")
        st.caption(explanation)

    with sub2:
        mode_label = st.radio(
            "Mode", ["Integrated (from a Brand Qualifier batch)", "Standalone - brand list", "Standalone - file"],
            key="sb_mode",
        )
        mode = {
            "Integrated (from a Brand Qualifier batch)": "integrated",
            "Standalone - brand list": "standalone_list",
            "Standalone - file": "standalone_file",
        }[mode_label]

        from_brand_batch = status = include = exclude = brands = input_path = ""
        if mode == "integrated":
            summaries = [str(p) for p in actions.list_brand_batch_summaries()]
            from_brand_batch = (
                st.selectbox("Brand batch summary", summaries, key="sb_summary")
                if summaries else st.text_input("Brand batch summary path", key="sb_summary_manual")
            )
            status = st.text_input("Status to select by default", value="PURSUE", key="sb_status")
            include = st.text_input("Also include (comma-separated), regardless of status", key="sb_include")
            exclude = st.text_input("Exclude (comma-separated), regardless of status", key="sb_exclude")
        elif mode == "standalone_list":
            brands = st.text_input("Brands (comma-separated)", key="sb_brands")
        else:
            input_path = st.text_input("Input file path (.csv / .json / .txt)", key="sb_input")

        concurrency2 = st.number_input("Concurrency", min_value=1, value=5, step=1, key="sb_conc")
        limit2 = st.number_input("Limit (0 = no limit)", min_value=0, value=0, step=1, key="sb_limit")
        dry_run = st.checkbox("Dry run (cache only, no paid calls)", key="sb_dry")
        no_web2 = st.checkbox("Disable web search (--no-web-search)", key="sb_noweb")

        command, explanation = cmd.supplier_batch_command(
            mode, from_brand_batch=from_brand_batch, status=status, include=include, exclude=exclude,
            brands=brands, input_path=input_path, concurrency=concurrency2, limit=limit2 or None,
            dry_run=dry_run, no_web_search=no_web2,
        )
        st.code(command, language="bash")
        st.caption(explanation)

    with sub3:
        st.markdown("**Resume a batch**")
        batch_ids = actions.list_supplier_batch_ids()
        batch_id = st.selectbox("Supplier batch id", batch_ids, key="resume_batch") if batch_ids else st.text_input("Supplier batch id", key="resume_batch_manual")
        resume_conc = st.number_input("Concurrency", min_value=1, value=5, step=1, key="resume_conc")
        rcmd, rexp = cmd.supplier_resume_command(batch_id, resume_conc)
        st.code(rcmd, language="bash")
        st.caption(rexp)

        st.markdown("**Render a report**")
        report_batch = st.selectbox("Batch id (optional)", ["(none)"] + batch_ids, key="report_batch_select")
        report_fragment = st.text_input("...or brand fragment", key="report_fragment_cmd")
        save_flag = st.checkbox("Also save to supplier_reports/", key="report_save_flag")
        rep_cmd, rep_exp = cmd.supplier_report_command(
            batch_id=report_batch if report_batch != "(none)" else "", brand_fragment=report_fragment, save=save_flag,
        )
        st.code(rep_cmd, language="bash")
        st.caption(rep_exp)

    with sub4:
        st.markdown("**Review a supplier relationship from the terminal instead of this app**")
        frag_s = st.text_input("Brand/supplier name fragment", key="cli_review_supplier")
        c1, e1 = cmd.supplier_review_command(frag_s or "<fragment>")
        st.code(c1, language="bash")
        st.caption(e1)

        st.markdown("**Review a brand result from the terminal instead of this app**")
        frag_b = st.text_input("Company name fragment", key="cli_review_brand")
        c2, e2 = cmd.brand_review_command(frag_b or "<fragment>")
        st.code(c2, language="bash")
        st.caption(e2)
