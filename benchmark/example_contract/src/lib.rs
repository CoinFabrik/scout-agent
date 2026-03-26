#![no_std]
use soroban_sdk::{
    contract, contractimpl, storage::PersistentDataKey, symbol_short, Address, Env, Map, Symbol, Vec,
};

#[contract]
pub struct CommunityTreasury;

#[derive(Clone)]
pub enum DataKey {
    Admin,
    Vault,
}

#[contractimpl]
impl CommunityTreasury {
    pub fn initialize(env: Env, admin: Address) {
        if env.storage().persistent().has(&DataKey::Admin) {
            panic!();
        }
        env.storage().persistent().set(&DataKey::Admin, &admin);
    }

    pub fn fund_treasury(env: Env, amount: i128) {
        let current: i128 = env.storage().persistent().get(&DataKey::Vault).unwrap_or(0);
        env.storage().persistent().set(&DataKey::Vault, &(current + amount));
    }

    pub fn distribute_rewards(env: Env, recipients: Vec<Address>) {
        let admin: Address = env.storage().persistent().get(&DataKey::Admin).expect("not init");
        admin.require_auth();

        if recipients.is_empty() {
            panic!();
        }

        let batch_size = recipients.len();
        let reward_amount = 1000 + (batch_size as i128 * 50);

        let mut unique_payouts: Map<Address, i128> = Map::new(&env);
        for recipient in recipients.iter() {
            unique_payouts.set(recipient, reward_amount);
        }

        let mut vault_balance: i128 = env.storage().persistent().get(&DataKey::Vault).unwrap_or(0);

        for (addr, amount) in unique_payouts.iter() {
            if vault_balance < amount {
                panic!("insufficient funds");
            }
            
            vault_balance -= amount;
            env.events().publish((symbol_short!("reward"), addr), amount);
        }

        env.storage().persistent().set(&DataKey::Vault, &vault_balance);
    }

    pub fn get_treasury_balance(env: Env) -> i128 {
        env.storage().persistent().get(&DataKey::Vault).unwrap_or(0)
    }
}
