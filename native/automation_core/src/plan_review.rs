//! OURS: one bounded planning draft. A plan review NEVER authorizes an action.
use crate::{conversation::Token, plan::Plan};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Ticket {
    pub owner: Token,
    pub revision: u64,
}
struct Draft {
    ticket: Ticket,
    plan: Plan,
    reviewed: Option<bool>,
}
#[derive(Default)]
pub struct Store {
    draft: Option<Draft>,
}
impl Store {
    pub fn stage(&mut self, owner: Token, plan: Plan) -> Result<Value, &'static str> {
        plan.validate()?;
        if self.draft.is_some() {
            return Err("plan_already_staged");
        }
        self.draft = Some(Draft {
            ticket: Ticket { owner, revision: 1 },
            plan,
            reviewed: None,
        });
        self.state()
    }
    pub fn state(&self) -> Result<Value, &'static str> {
        let draft = self.draft.as_ref().ok_or("no_plan_draft")?;
        Ok(
            json!({"status":"plan_draft","ticket":draft.ticket,"plan":draft.plan,"plan_reviewed":draft.reviewed==Some(true),
            "action_approval":false,"document_revision":null,"revision_kind":"planning_draft_only"}),
        )
    }
    pub fn review(&mut self, ticket: Ticket, accepted: bool) -> Result<Value, &'static str> {
        let draft = self.draft.as_mut().ok_or("no_plan_draft")?;
        if draft.ticket != ticket {
            return Err("stale_plan_revision");
        }
        if draft.reviewed.is_some() {
            return Err("plan_already_reviewed");
        }
        draft.reviewed = Some(accepted);
        self.state()
    }
    pub fn revise(&mut self, ticket: Ticket, plan: Plan) -> Result<Vec<String>, &'static str> {
        plan.validate()?;
        let draft = self.draft.as_mut().ok_or("no_plan_draft")?;
        if draft.ticket != ticket {
            return Err("stale_plan_revision");
        }
        if draft.ticket.revision >= 8 {
            return Err("plan_revision_limit");
        }
        let old: BTreeMap<_, _> = draft
            .plan
            .steps
            .iter()
            .map(|step| (step.id.clone(), serde_json::to_value(step).unwrap()))
            .collect();
        let new: BTreeMap<_, _> = plan
            .steps
            .iter()
            .map(|step| (step.id.clone(), serde_json::to_value(step).unwrap()))
            .collect();
        let mut changed: BTreeSet<String> = old
            .keys()
            .chain(new.keys())
            .filter(|id| old.get(*id) != new.get(*id))
            .cloned()
            .collect();
        if draft.plan.units != plan.units
            || draft.plan.intent != plan.intent
            || draft.plan.scope_requirement != plan.scope_requirement
            || draft.plan.goal != plan.goal
            || draft.plan.questions != plan.questions
        {
            changed.extend(old.keys().chain(new.keys()).cloned());
        }
        // validate() requires topological order, so one forward pass is sufficient.
        for step in &plan.steps {
            if step.depends_on.iter().any(|id| changed.contains(id)) {
                changed.insert(step.id.clone());
            }
        }
        draft.ticket.revision += 1;
        draft.plan = plan;
        draft.reviewed = None;
        Ok(changed.into_iter().collect())
    }
    pub fn rebind_planning_owner(&mut self, owner: Token) {
        if let Some(draft) = &mut self.draft {
            draft.ticket.owner = owner;
        }
    }
}
